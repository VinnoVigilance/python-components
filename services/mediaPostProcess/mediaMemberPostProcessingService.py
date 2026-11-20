"""Validate semantic extraction, compare fingerprints, and persist history."""
from __future__ import annotations

from utils.hashing import calculate_record_hash
from infrastructure.database.connection import get_db_connection
from repositories.mediaPostProcess import mediaMemberPostProcessRepository as repository


MAPPING_VERSION = '1.0'


def _normalized_unique(values):
    if not isinstance(values, list):
        raise ValueError('Expected a list')
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError('Expected nonblank strings')
    return sorted(set(values))


def prepare_members(result, *, title, body, extra_columns=False):
    """Hash semantic names/evidence, not model scores, votes, run IDs or time.

    Mention offsets and evidence sentences ARE semantic: their changes version
    the whole member set. Output order does not affect the hash.
    """
    if not isinstance(result, dict) or result.get('status') != 'COMPLETED':
        raise ValueError('Extraction was not completed successfully')
    if result.get('schemaVersion') != '1.0' or not isinstance(result.get('entities'), list):
        raise ValueError('Incompatible entity extraction schema')
    output, seen = [], set()
    for entity in result['entities']:
        if not isinstance(entity, dict):
            raise ValueError('Invalid entity')
        name, kind = entity.get('memberName'), entity.get('entityType')
        if not isinstance(name, str) or not name.strip() or kind not in ('INDIVIDUAL','ORGANIZATION'):
            raise ValueError('Invalid memberName/entityType')
        aliases = _normalized_unique(entity.get('aliases'))
        grouped = entity.get('groupingEvidence')
        mentions = entity.get('mentions')
        if not isinstance(grouped, list) or not isinstance(mentions, list) or not mentions:
            raise ValueError('Member requires groupingEvidence and at least one mention')
        semantic_mentions = []
        for mention in mentions:
            if not isinstance(mention, dict):
                raise ValueError('Invalid mention')
            field = mention.get('field')
            if field not in ('title','body'):
                raise ValueError('Invalid mention field')
            source = title if field == 'title' else body
            start, end, surface = mention.get('start'), mention.get('end'), mention.get('text')
            if (type(start) is not int or type(end) is not int or not isinstance(surface,str)
                    or not 0 <= start < end <= len(source) or source[start:end] != surface):
                raise ValueError('Mention span does not match full article text')
            sentence = mention.get('evidenceSentence')
            if not isinstance(sentence,str) or not sentence.strip():
                raise ValueError('Missing evidenceSentence')
            semantic_mentions.append({'field':field,'start':start,'end':end,'text':surface,
                                      'evidenceSentence':sentence})
        if not all(isinstance(item, dict) for item in grouped):
            raise ValueError('Invalid groupingEvidence item')
        semantic = {
            'schemaVersion': result['schemaVersion'], 'entityType':kind, 'memberName':name,
            'aliases':aliases,
            'mentions': sorted(semantic_mentions, key=calculate_record_hash),
            'groupingEvidence':sorted(grouped, key=calculate_record_hash),
        }
        digest = calculate_record_hash(semantic)
        if digest in seen:
            raise ValueError('Duplicate identical entity in one extraction result')
        seen.add(digest)
        # When dedicated columns exist, names/type are kept there (not copied to JSON).
        member_payload = {key:value for key,value in semantic.items()
                          if key not in ('memberName','entityType')} if extra_columns else semantic
        output.append({'hash':digest,'entity_type':kind,'member_name':name,
                       'payload':member_payload})
    return output


def input_fingerprint(source, calculation_version):
    return calculate_record_hash({
        'coreId':source['core_id'], 'coreVersion':source['version'],
        'adverseStatus':'SCORED', 'adversePositive':source['positive'],
        'calculationVersion':calculation_version, 'mappingVersion':MAPPING_VERSION,
    })


class MediaMemberPostProcessingService:
    def __init__(self, *, extra_columns, extractor_factory):
        self.extra_columns = extra_columns
        self._factory = extractor_factory
        self._extractor = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self._extractor is not None:
            self._extractor.close()

    def _get_extractor(self):
        if self._extractor is None:
            self._extractor = self._factory()
        return self._extractor

    def read(self, vv_media_id):
        with get_db_connection() as db:
            return repository.snapshot(db, vv_media_id)

    def _persist(self, snapshot, entities, manifest):
        with get_db_connection() as db:
            return repository.save_members(db, snapshot, entities, manifest,
                                           extra_columns=self.extra_columns)

    def prepare(self, snapshot, *, force=False):
        """Return (status, pending_work), with no DB connection during inference."""
        if snapshot['deleted'] or not snapshot['positive']:
            return ('DELETED' if snapshot['deleted'] else 'NON_ADVERSE'), None
        if not (snapshot['title'].strip() or snapshot['body'].strip()):
            raise repository.PendingSource('Positive adverse article has no extractable text')
        extractor = self._get_extractor()
        calculation = extractor.metadata['calculationVersion']
        fingerprint = input_fingerprint(snapshot, calculation)
        if not force:
            with get_db_connection() as db:
                active = repository.current_members(db, snapshot['vv_media_id'])
            if active and all(row['media_record_id'] == snapshot['core_id'] and
                              row['media_version'] == snapshot['version'] and
                              (row['processor_manifest'] or {}).get('inputKey') == fingerprint
                              for row in active):
                return 'ALREADY_PROCESSED', None
        return 'EXTRACT', {'snapshot':snapshot,'fingerprint':fingerprint}

    def expire(self, snapshot):
        return self._persist(snapshot, [], {'reason':'DELETED' if snapshot['deleted'] else 'NOT_ADVERSE'})

    def extract_and_save_batch(self, pending):
        """Batch inference, then one independent transaction per article."""
        if not pending:
            return []
        extractor = self._get_extractor()
        inputs = [(item['snapshot']['title'], item['snapshot']['body']) for item in pending]
        results = extractor.extract_many(inputs, batch_size=len(inputs))
        if len(results) != len(pending):
            raise ValueError('Entity extraction returned a different number of results')
        outputs = []
        metadata = extractor.metadata
        for item, result in zip(pending, results):
            try:
                if result.get('calculationVersion') != metadata['calculationVersion']:
                    raise ValueError('Entity extraction calculationVersion changed mid-run')
                snapshot = item['snapshot']
                prepared = prepare_members(result, title=snapshot['title'], body=snapshot['body'],
                                           extra_columns=self.extra_columns)
                manifest = {
                    'entityExtraction':{k:v for k,v in metadata.items() if k != 'newsModelSeconds'},
                    'mappingVersion': MAPPING_VERSION, 'inputKey':item['fingerprint'],
                    'analysisId':snapshot['analysis_id'],
                    'analysisSourceVersion':snapshot['analysis_source_version'],
                }
                outputs.append((snapshot['vv_media_id'], self._persist(snapshot, prepared, manifest), None))
            except Exception as error:
                outputs.append((item['snapshot']['vv_media_id'], 'FAILED', error))
        return outputs
