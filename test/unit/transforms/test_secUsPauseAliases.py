"""Read the actual project alias rules; use the bundle override for offline QA."""
import copy
import json
import os
import unittest
from pathlib import Path

import pandas as pd

from transforms.preProcessingEngine import PreProcessingEngine
from transforms.preNormalization import PreNormalizationEngine
from transforms.fieldMapper import MappingEngine, load_rules
from transforms.postNormalization import PostNormalizationEngine


ROOT = Path(__file__).resolve().parents[3]
SOURCE = 'SEC_US_PAUSE'


class TestSecUsPauseAliases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((ROOT / 'test/fixtures/watchlist/sec_us_pause/alias_titles.json')
                              .read_text(encoding='utf-8'))
        bundle = os.environ.get('SEC_PAUSE_ALIAS_BUNDLE')
        if bundle:
            pre_df = pd.read_excel(bundle, sheet_name='PreNormalization')
            mapping_file = bundle
        else:
            pre_df = pd.read_excel(ROOT / 'data/rules/preNormalization.xlsx')
            mapping_file = ROOT / 'data/rules/mapping.xlsx'
        rules = pre_df.fillna('')
        for target in ('pause_primary_name', 'pause_alias_blob', 'pause_aliases'):
            matches = rules[(rules['source'] == SOURCE) & (rules['target_field'] == target)]
            if len(matches) != 1:
                raise AssertionError(f'Expected exactly one SEC_US_PAUSE rule for {target}.')
        cls.pre = PreNormalizationEngine(pre_df, pd.DataFrame([
            {'source': SOURCE, 'entity_field': 'entity_type'}]))
        cls.mapper = MappingEngine(load_rules(mapping_file, SOURCE))
        cls.post = PostNormalizationEngine(
            pd.read_excel(ROOT / 'data/rules/postNormalization.xlsx'), {'date_order': 'MDY'})

    def raw(self, title, url='https://example.test/alerts/one', notes=''):
        record = {'entity_type': 'Entity', 'detail_url': url,
                  'detail': {'name': title, 'notes': notes}, 'list': {}}
        return PreProcessingEngine().generate_composite_id(record, {
            'fields': ['detail_url'], 'output_field': 'source_record_id',
            'prefix': 'SEC-US-PAUSE'})

    def normalize(self, raw):
        pre = self.pre.pre_normalize_record(SOURCE, raw)
        final = self.post.post_normalize_record(self.mapper.map_record(pre))
        return pre, final

    def names(self, title, **kwargs):
        _, final = self.normalize(self.raw(title, **kwargs))
        return (final['Names'][0]['Name'] if final['Names'] else '',
                [a['Alias'] for a in final['Aliases']])

    def test_all_32_real_titles_have_expected_primary_names_and_aliases(self):
        self.assertEqual(len(self.cases), 32)
        aliases = 0
        for case in self.cases:
            with self.subTest(title=case['title']):
                _, final = self.normalize(self.raw(case['title'], case['detail_url']))
                self.assertEqual(final['Names'][0]['Name'], case['primary'])
                self.assertEqual([a['Alias'] for a in final['Aliases']], case['aliases'])
                self.assertEqual({a['AliasType'] for a in final['Aliases']}, {'AKA'})
                self.assertEqual(final['EntityId'], case['entity_id'])
                aliases += len(final['Aliases'])
        self.assertEqual(aliases, 33)

    def test_names_without_alias_markers_are_preserved(self):
        for title in ('Example Ltd.', 'AKA Advisors LLC', '  AKA Advisors LLC  ',
                      'Akamai Holdings', 'Example (Holdings) Ltd.'):
            with self.subTest(title=title):
                self.assertEqual(self.names(title), (title.strip(), []))

    def test_multiple_markers_keep_each_alias(self):
        self.assertEqual(self.names('Example LLC, a.k.a. First Name, AKA Second Name'),
                         ('Example LLC', ['First Name', 'Second Name']))

    def test_parenthesized_alias_wrapper_is_removed(self):
        self.assertEqual(self.names('Example Group (aka Other Group)'),
                         ('Example Group', ['Other Group']))

    def test_parentheses_inside_an_alias_are_preserved(self):
        for title, alias in [
            ('Example aka Other Group (SCC)', 'Other Group (SCC)'),
            ('Example (aka Other Group (Trading))', 'Other Group (Trading)'),
            ('Example aka Other (sic) LLC', 'Other (sic) LLC'),
        ]:
            with self.subTest(title=title):
                self.assertEqual(self.names(title), ('Example', [alias]))

    def test_commas_and_company_suffix_periods_are_preserved(self):
        self.assertEqual(self.names('Example, Inc., a.k.a. Other Co., Inc.'),
                         ('Example, Inc.', ['Other Co., Inc.']))

    def test_raw_title_and_input_record_are_preserved(self):
        raw = self.raw('Example LLC aka Other LLC')
        before = copy.deepcopy(raw)
        pre, _ = self.normalize(raw)
        self.assertEqual(raw, before)
        self.assertEqual(pre['detail']['name'], before['detail']['name'])

    def test_genuine_firm_names_in_notes_are_not_aliases(self):
        self.assertEqual(self.names('12 West Capital Management',
            notes='Genuine firm: Example LP aka Genuine Name. CRD 160197.'),
            ('12 West Capital Management', []))

    def test_generated_id_stays_internal_and_is_stable_on_title_changes(self):
        _, first = self.normalize(self.raw('Example LLC aka Other LLC'))
        _, corrected = self.normalize(self.raw('Corrected Example LLC aka Other LLC'))
        _, other = self.normalize(self.raw('Example LLC aka Other LLC',
                                          url='https://example.test/alerts/two'))
        self.assertEqual(first['EntityId'], corrected['EntityId'])
        self.assertNotEqual(first['EntityId'], other['EntityId'])
        self.assertEqual(first['Sources'][0]['SourceRecordId'], '')
        self.assertEqual(first['Identifiers'], [])

    def test_domain_stated_as_alias_remains_an_entity_alias(self):
        _, final = self.normalize(self.raw('Example AKA example.org'))
        self.assertEqual(final['EntityType'], 'Entity')
        self.assertEqual(final['Aliases'][0]['Alias'], 'example.org')
        self.assertEqual(final['Contacts'], [])

    def test_other_sources_do_not_receive_these_rules(self):
        raw = self.raw('Example AKA Other')
        result = self.pre.pre_normalize_record('OTHER_SOURCE', raw)
        self.assertNotIn('pause_primary_name', result)
        self.assertNotIn('pause_aliases', result)

    def test_aka_is_already_registered_in_picklist(self):
        picks = pd.read_excel(ROOT / 'data/rules/pickLists.xlsx').fillna('')
        self.assertTrue(any(r['Path'] == 'Aliases.AliasType' and r['Value'] == 'AKA'
                            for _, r in picks.iterrows()))


if __name__ == '__main__':
    unittest.main()
