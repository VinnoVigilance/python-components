"""Unit tests. No DB/model weights are needed; DB Integration is separate."""
import importlib.util
import sys
import types
from contextlib import nullcontext
from datetime import date
from unittest.mock import Mock

import pytest

# The production modules use psycopg2. Allow pure-logic unit tests in minimal
# CI environments without PostgreSQL's optional runtime packages installed.
if importlib.util.find_spec('psycopg2') is None:
    psycopg2 = types.ModuleType('psycopg2')
    psycopg2.__path__ = []
    extras = types.ModuleType('psycopg2.extras')
    extras.Json = lambda value: value
    extras.RealDictCursor = type('RealDictCursor', (), {})
    pool = types.ModuleType('psycopg2.pool')
    pool.ThreadedConnectionPool = object
    psycopg2.extras, psycopg2.pool = extras, pool
    sys.modules.update({'psycopg2':psycopg2,'psycopg2.extras':extras,'psycopg2.pool':pool})

from services.mediaPostProcess.mediaMemberPostProcessingService import (
    prepare_members, input_fingerprint, MediaMemberPostProcessingService)
from pipelines import mediaMemberPostProcessingPipeline as pipeline
from repositories.mediaPostProcess import mediaMemberPostProcessRepository as repo


def news_result(*, name='Alex', field='title', start=0, evidence='Alex is involved.'):
    return {
        'status':'COMPLETED','schemaVersion':'1.0','calculationVersion':'model-v1',
        'entities':[{'entityType':'INDIVIDUAL','memberName':name,'aliases':['A. Reed','Alex'],
            'groupingEvidence':[{'rule':'acronym'}],
            'mentions':[{'text':'Alex','field':field,'start':start,'end':start+4,
                         'evidenceSentence':evidence,'votes':3,'scores':{'m':0.98},'models':['m']}] }],
        'unresolvedNameLinks':[],
    }


def source(*, version=1, core_id=9001, positive=True):
    return {'vv_media_id':410,'core_id':core_id,'version':version,'deleted':False,
        'analysis_id':500,'analysis_hash':'adverse-hash','analysis_source_version':version,
        'positive':positive,'title':'Alex','body':'Some unrelated body.'}


@pytest.mark.unit
def test_hash_ignores_order_and_model_telemetry():
    first = news_result()
    second = news_result()
    second['entities'][0]['aliases'].reverse()
    second['entities'][0]['mentions'][0].update(votes=2,scores={'m':0.11},models=['n'])
    assert prepare_members(first, title='Alex', body='')[0]['hash'] == (
        prepare_members(second, title='Alex', body='')[0]['hash'])


@pytest.mark.unit
def test_evidence_and_offsets_change_hash():
    first = news_result()
    second = news_result(evidence='Another sentence about Alex.')
    assert prepare_members(first,title='Alex',body='')[0]['hash'] != (
        prepare_members(second,title='Alex',body='')[0]['hash'])
    moved = news_result(field='body',start=0)
    assert prepare_members(first,title='Alex',body='Alex in body')[0]['hash'] != (
        prepare_members(moved,title='Alex',body='Alex in body')[0]['hash'])


@pytest.mark.unit
def test_name_and_entity_type_live_inside_json_for_architect_schema():
    record = prepare_members(news_result(),title='Alex',body='',extra_columns=False)[0]
    assert record['member_name'] == 'Alex'
    assert record['payload']['memberName'] == 'Alex'
    assert record['payload']['entityType'] == 'INDIVIDUAL'
    assert record['payload']['mentions'][0]['evidenceSentence']


@pytest.mark.unit
@pytest.mark.parametrize('value', [
    {'status':'SKIPPED_EMPTY_INPUT','schemaVersion':'1.0','entities':[]},
    {'status':'COMPLETED','schemaVersion':'2','entities':[]},
])
def test_non_completed_or_unknown_contract_never_closes_members(value):
    with pytest.raises(ValueError):
        prepare_members(value,title='Alex',body='')


@pytest.mark.unit
def test_successful_zero_is_valid():
    result = news_result()
    result['entities'] = []
    assert prepare_members(result,title='Alex',body='') == []


@pytest.mark.unit
def test_broken_mention_span_rejected():
    with pytest.raises(ValueError, match='span'):
        prepare_members(news_result(),title='Other title',body='')


@pytest.mark.unit
def test_duplicate_entity_rejected():
    result = news_result()
    result['entities'].append(result['entities'][0].copy())
    with pytest.raises(ValueError, match='Duplicate'):
        prepare_members(result,title='Alex',body='')


@pytest.mark.unit
def test_input_fingerprint_changes_with_core_version_not_summary():
    old = source()
    new = source(core_id=9008, version=2)
    assert input_fingerprint(old,'model-v1') != input_fingerprint(new,'model-v1')
    assert input_fingerprint(old,'model-v1') != input_fingerprint(old,'model-v2')


@pytest.mark.unit
def test_positive_empty_input_is_pending_before_extractor(monkeypatch):
    service = MediaMemberPostProcessingService(extra_columns=False,extractor_factory=lambda:None)
    with pytest.raises(repo.PendingSource):
        service.prepare({**source(), 'title':'', 'body':''})


@pytest.mark.unit
def test_nonadverse_and_deleted_never_start_models(monkeypatch):
    factory = Mock(side_effect=AssertionError('model must not load'))
    service = MediaMemberPostProcessingService(extra_columns=False,extractor_factory=factory)
    assert service.prepare(source(positive=False))[1] is None
    assert service.prepare({'vv_media_id':410,'core_id':9001,'version':1,'deleted':True})[1] is None
    factory.assert_not_called()


@pytest.mark.unit
def test_previously_processed_members_shortcircuit_only_same_input(monkeypatch):
    extractor = Mock(metadata={'calculationVersion':'model-v1'})
    service = MediaMemberPostProcessingService(extra_columns=False,extractor_factory=lambda:extractor)
    monkeypatch.setattr('services.mediaPostProcess.mediaMemberPostProcessingService.get_db_connection',
                        lambda: nullcontext('conn'))
    old = source()
    monkeypatch.setattr(repo,'current_members',lambda db,vv: [{
        'media_record_id':old['core_id'],'media_version':old['version'],
        'processor_manifest':{'inputKey':input_fingerprint(old,'model-v1')}}])
    assert service.prepare(old)[0]=='ALREADY_PROCESSED'
    assert service.prepare(source(version=2,core_id=9008))[0]=='EXTRACT'
    assert service.prepare(old,force=True)[0]=='EXTRACT'


@pytest.mark.unit
def test_validation_of_mode_and_explicit_incremental_date():
    with pytest.raises(ValueError):pipeline.run_media_member_post_processing('incremental')
    with pytest.raises(ValueError):pipeline.run_media_member_post_processing('reprocess')
    with pytest.raises(ValueError):pipeline.run_media_member_post_processing('initial',vv_media_ids=[410])
    with pytest.raises(ValueError):pipeline.run_media_member_post_processing(
        'incremental',effective_date=date(2026,11,20),through_date=date(2026,11,19))


@pytest.mark.unit
def test_incremental_uses_both_delta_and_analysis_feeds(monkeypatch):
    connection = object()
    monkeypatch.setattr(pipeline,'get_db_connection',lambda:nullcontext(connection))
    monkeypatch.setattr(repo,'preflight',lambda conn:False)
    monkeypatch.setattr(repo,'scan_bounds',lambda conn:(9999,40,70))
    validation=Mock()
    monkeypatch.setattr(repo,'validate_delta_references',validation)
    read=Mock(side_effect=[[410,411],[]])
    monkeypatch.setattr(repo,'find_incremental_batch',read)
    monkeypatch.setattr(repo,'find_initial_batch',Mock(side_effect=AssertionError('Full core scan forbidden')))
    data={410:source(),411:{**source(core_id=9002), "vv_media_id":411}}
    class FakeService:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def read(self,vv):return data[vv]
        def prepare(self,row,force=False):return ('NON_ADVERSE',None)
        def expire(self,row):return 'NON_ADVERSE'
    monkeypatch.setattr(pipeline,'MediaMemberPostProcessingService',FakeService)
    result=pipeline.run_media_member_post_processing('incremental',effective_date=date(2026,11,20))
    assert result['run_status']=='SUCCESS'
    assert result['counts']['scanned']==2
    assert result['counts']['non_adverse']==2
    read.assert_any_call(connection,0,40,70,date(2026,11,20),date(2026,11,20),16)
    validation.assert_called_once()


@pytest.mark.unit
def test_reprocess_forces_model_even_with_current_members(monkeypatch):
    connection = object()
    monkeypatch.setattr(pipeline,'get_db_connection',lambda:nullcontext(connection))
    monkeypatch.setattr(repo,'preflight',lambda conn:False)
    monkeypatch.setattr(repo,'scan_bounds',lambda conn:(9999,40,70))
    extractor=Mock(metadata={'calculationVersion':'model-v1'})
    extractor.extract_many.return_value=[news_result()]
    extractor.close.return_value=None
    monkeypatch.setattr('services.mediaPostProcess.mediaMemberPostProcessingService.get_db_connection',
                        lambda:nullcontext(connection))
    monkeypatch.setattr(repo,'snapshot',lambda conn,vv:source())
    monkeypatch.setattr(repo,'current_members',lambda conn,vv:[])
    saved=Mock(return_value='UNCHANGED')
    monkeypatch.setattr(repo,'save_members',saved)
    result=pipeline.run_media_member_post_processing('reprocess',vv_media_ids=[410],
                         extractor_factory=lambda:extractor)
    assert result['run_status']=='SUCCESS'
    assert result['counts']['unchanged']==1
    extractor.extract_many.assert_called_once()
    saved.assert_called_once()

@pytest.mark.unit
def test_batch_failure_isolates_one_bad_article(monkeypatch):
    connection = object()
    monkeypatch.setattr(pipeline,'get_db_connection',lambda:nullcontext(connection))
    monkeypatch.setattr(repo,'preflight',lambda db:False)
    monkeypatch.setattr(repo,'scan_bounds',lambda db:(10000,10,10))
    monkeypatch.setattr(repo,'find_initial_batch',Mock(side_effect=[[(9001,410),(9002,411)],[]]))
    data={410:source(),411:{**source(core_id=9002), "vv_media_id":411}}
    class FakeService:
        def __init__(self, **kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def read(self,vv):return data[vv]
        def prepare(self,row,force=False):return 'EXTRACT',{'snapshot':row}
        def extract_and_save_batch(self,tasks):
            if any(task['snapshot']['vv_media_id']==411 for task in tasks):
                raise ValueError('bad article')
            return [(410,'INSERTED',None)]
    monkeypatch.setattr(pipeline,'MediaMemberPostProcessingService',FakeService)
    result=pipeline.run_media_member_post_processing('initial',batch_size=2)
    assert result['run_status']=='PARTIAL'
    assert result['counts']['inserted']==1
    assert result['counts']['failed']==1
    assert result['errors'][0]['vv_media_id']==411


@pytest.mark.unit
def test_job_does_not_run_without_advisory_lock(monkeypatch):
    from jobs import mediaMemberPostProcessingJob as job
    class FakeLog:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def stamp(self):return '2026-11-20T12:00:00+00:00'
        def event(self,*args,**kwargs):pass
        def write_block(self,*args,**kwargs):pass
    monkeypatch.setattr(job,'JobLog',FakeLog)
    monkeypatch.setattr(job,'configure_logging',lambda:None)
    monkeypatch.setattr(job,'advisory_job_lock',lambda lock:nullcontext(False))
    run=Mock(side_effect=AssertionError('must not run'))
    monkeypatch.setattr(job,'run_media_member_post_processing',run)
    assert job.main(['--mode','initial'])==2
    run.assert_not_called()


@pytest.mark.unit
def test_job_cli_passes_explicit_date_to_incremental(monkeypatch):
    from jobs import mediaMemberPostProcessingJob as job
    class FakeLog:
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def stamp(self):return '2026-11-20T12:00:00+00:00'
        def event(self,*args,**kwargs):pass
        def write_block(self,*args,**kwargs):pass
    monkeypatch.setattr(job,'JobLog',FakeLog)
    monkeypatch.setattr(job,'configure_logging',lambda:None)
    monkeypatch.setattr(job,'advisory_job_lock',lambda lock:nullcontext(True))
    run=Mock(return_value={'run_status':'SUCCESS','counts':{'scanned':0},'duration_seconds':0})
    monkeypatch.setattr(job,'run_media_member_post_processing',run)
    assert job.main(['--mode','incremental','--effective-date','2026-11-20'])==0
    assert run.call_args.kwargs['effective_date']==date(2026,11,20)
