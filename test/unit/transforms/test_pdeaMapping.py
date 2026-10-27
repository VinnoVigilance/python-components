"""Offline checks of installed PDEA Excel mapping and the existing transform chain.

PDEA_RULES_WORKBOOK can point to the supplied additions workbook for review.
By default these tests check the actual runtime workbooks after installation.
"""

import json
import os
import unittest
from copy import deepcopy
from pathlib import Path

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS

import pandas as pd

from parsing.ocr.ocrRecordExtractor import OcrRecordExtractor
from transforms.fieldMapper import MappingEngine, load_rules
from transforms.postNormalization import PostNormalizationEngine
from transforms.preNormalization import PreNormalizationEngine
from transforms.preProcessingEngine import PreProcessingEngine


ROOT = Path(__file__).resolve().parents[3]
RULES = ROOT / 'data/rules'
SOURCE = 'PDEA_MOST_WANTED'


class TestPdeaMapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone = os.environ.get('PDEA_RULES_WORKBOOK')
        cls.mapping_path = Path(standalone) if standalone else RULES / 'mapping.xlsx'
        mapping = pd.read_excel(cls.mapping_path, keep_default_na=False)
        for column in (SOURCE, SOURCE + ' TYPE'):
            if column not in mapping:
                raise AssertionError('Add Mapping!F1:G429 as two new columns, aligned with the existing field rows.')
        cls.mapping = mapping
        cls.source_rows = pd.read_excel(
            standalone or RULES / 'sourceConfig.xlsx',
            sheet_name='sourceConfig' if standalone else 0,
            keep_default_na=False,
        )
        cls.source_rows = cls.source_rows[cls.source_rows['source'] == SOURCE]
        if len(cls.source_rows) != 1:
            raise AssertionError('Add sourceConfig!A2:C2 exactly once to sourceConfig.xlsx.')
        cls.picklists = pd.read_excel(RULES / 'pickLists.xlsx', keep_default_na=False)
        if standalone:
            cls.picklists = pd.concat([
                cls.picklists,
                pd.read_excel(standalone, sheet_name='pickLists', keep_default_na=False),
            ], ignore_index=True)
        cls.config = deepcopy(WATCHLIST_CONFIGS["PDEA_MOST_WANTED"])
        document = json.loads((ROOT / 'test/fixtures/watchlist/pdea_most_wanted/ocr_document.json').read_text(encoding='utf-8'))
        layout = json.loads((ROOT / 'config/ocrLayouts/pdea_most_wanted.json').read_text(encoding='utf-8'))
        cls.raw = PreProcessingEngine().preprocess(
            OcrRecordExtractor(layout).extract(document), cls.config['preprocessing'],
        )
        cls.pre = PreNormalizationEngine(
            pd.read_excel(RULES / 'preNormalization.xlsx'), cls.source_rows,
        )
        cls.mapper = MappingEngine(load_rules(cls.mapping_path, SOURCE))
        cls.post = PostNormalizationEngine(
            pd.read_excel(RULES / 'postNormalization.xlsx'), cls.config,
        )

    def canonical(self, record):
        normalized = self.pre.pre_normalize_record(SOURCE, record)
        return self.post.post_normalize_record(self.mapper.map_record(normalized))

    def test_all_sixteen_names_are_mapped_without_changes_or_extra_people(self):
        result = [self.canonical(r) for r in self.raw]
        self.assertEqual(len(result), 16)
        self.assertEqual([r['Names'][0]['Name'] for r in result], [r['primary_name'] for r in self.raw])
        self.assertTrue(all(len(r['Names']) == 1 for r in result))

    def test_internal_identity_and_entity_type(self):
        for raw in self.raw:
            record = self.canonical(raw)
            self.assertEqual(record['EntityId'], raw['unique_id'])
            self.assertEqual(record['EntityType'], 'Individual')

    def test_generated_id_never_populates_source_record_id(self):
        for raw in self.raw:
            source = self.canonical(raw)['Sources'][0]
            self.assertEqual(source['SourceRecordId'], '')

    def test_source_metadata(self):
        source = self.canonical(self.raw[0])['Sources'][0]
        for field, expected in {
            'SourceName': 'PDEA', 'ListName': SOURCE,
            'SourceType': 'Official', 'DatasetCategory': 'Law Enforcement',
        }.items():
            self.assertEqual(source[field], expected)

    def test_source_url_points_to_listing_not_local_path_or_card_id(self):
        self.assertEqual(self.canonical(self.raw[0])['Sources'][0]['SourceURL'], 'https://pdea.gov.ph/most-wanted/')

    def test_source_note_explains_ocr_and_small_print_limit(self):
        note = self.canonical(self.raw[0])['Sources'][0]['Note']
        self.assertIn('OCR', note)
        self.assertIn('unverified', note)

    def test_names_use_existing_primary_name_picklist_value(self):
        self.assertEqual(self.canonical(self.raw[0])['Names'][0]['NameType'], 'Primary Name')

    def test_name_search_enrichment_still_runs(self):
        name = self.canonical(self.raw[0])['Names'][0]
        self.assertTrue(name['Normalized_Name'])
        self.assertTrue(name['Search_Tokens'])

    def test_no_personal_attributes_dates_or_country_are_guessed(self):
        record = self.canonical(self.raw[0])
        self.assertFalse(record['Gender'])
        self.assertFalse(record['DateAdded'])
        self.assertFalse(record['DateUpdated'])
        for field in ('Countries', 'Dates', 'Addresses', 'Places', 'Contacts', 'Occupations'):
            self.assertEqual(record[field], [])

    def test_aliases_case_numbers_rewards_and_small_print_are_not_structured(self):
        record = self.canonical(self.raw[-2])
        for field in ('Aliases', 'Identifiers', 'additionalInfo', 'Comments'):
            self.assertEqual(record[field], [])
        self.assertNotIn('CC02-1576', json.dumps(record))

    def test_no_arrest_death_or_conviction_is_inferred_from_crosses(self):
        for raw in self.raw:
            record = self.canonical(raw)
            self.assertEqual(record['Measures'], [])
            self.assertEqual(record['Programs'], [])
            self.assertNotIn('status', record)

    def test_poster_is_not_mapped_as_a_personal_portrait(self):
        self.assertEqual(self.canonical(self.raw[0])['Attachments'], [])

    def test_review_diagnostic_and_original_evidence_are_preserved_in_raw(self):
        raw = next(r for r in self.raw if r['_ocr']['needs_review'])
        before = deepcopy(raw)
        record = self.canonical(raw)
        self.assertEqual(raw, before)
        self.assertEqual(record['Names'][0]['Name'], 'RONALD YAO Y CRUZ')
        self.assertNotIn('human_reviewed', record)

    def test_raw_external_id_configuration_uses_internal_id(self):
        row = self.source_rows.iloc[0]
        self.assertEqual(row['entity_field'], 'entity_type')
        self.assertEqual(row['External_Id_Path'], 'unique_id')

    def test_all_categorical_constants_exist_in_picklists(self):
        record = self.canonical(self.raw[0])
        values = {
            'EntityType': 'Individual',
            'Names.NameType': 'Primary Name',
            'Sources.SourceType': record['Sources'][0]['SourceType'],
            'Sources.DatasetCategory': record['Sources'][0]['DatasetCategory'],
            'Sources.SourceName': 'PDEA',
            'Sources.ListName': SOURCE,
        }
        for path, value in values.items():
            matches = self.picklists[(self.picklists['Path'] == path) & (self.picklists['Value'] == value)]
            self.assertEqual(len(matches), 1, f'Expected one PickList entry for {path}: {value}')
        listing = self.picklists[(self.picklists['Path'] == 'Sources.ListName') & (self.picklists['Value'] == SOURCE)].iloc[0]
        self.assertEqual(listing['Parent Path'], 'Sources.SourceName')
        self.assertEqual(listing['Parent Value'], 'PDEA')


if __name__ == '__main__':
    unittest.main()
