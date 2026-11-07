"""Offline regressions for real SEC contact formats and generic list extraction."""
import copy
import json
import os
import unittest
from pathlib import Path

import pandas as pd

from transforms.preNormalization import PreNormalizationEngine, RegexExtractAllHandler
from transforms.preProcessingEngine import PreProcessingEngine
from transforms.fieldMapper import MappingEngine, load_rules
from transforms.postNormalization import PostNormalizationEngine

ROOT = Path(__file__).resolve().parents[3]
SOURCE = 'SEC_US_PAUSE'


class TestRegexExtractAllLists(unittest.TestCase):
    def test_string_and_none_keep_existing_behavior(self):
        h = RegexExtractAllHandler()
        self.assertEqual(h.normalize('x=12; x=34', r'x=(\d+)'), ['12', '34'])
        self.assertEqual(h.normalize(None, r'\d+'), [])
        self.assertEqual(h.normalize(123, r'\d+'), ['123'])

    def test_every_list_section_contributes_matches(self):
        self.assertEqual(RegexExtractAllHandler().normalize(['x=12', 'x=34; x=56'],
                                                           r'x=(\d+)'), ['12', '34', '56'])

    def test_nested_lists_are_flattened_without_mutating_or_deduplicating(self):
        values = ['x=12', [None, 'x=12'], []]
        before = copy.deepcopy(values)
        self.assertEqual(RegexExtractAllHandler().normalize(values, r'x=(\d+)'), ['12', '12'])
        self.assertEqual(values, before)


class TestSecUsPauseContactRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = json.loads((ROOT / 'test/fixtures/watchlist/sec_us_pause/contact_edge_cases.json')
                                 .read_text(encoding='utf-8'))
        pre_path = os.environ.get('SEC_PAUSE_RULES_FILE', ROOT / 'data/rules/preNormalization.xlsx')
        mapping = os.environ.get('SEC_PAUSE_MAPPING_FILE', ROOT / 'data/rules/mapping.xlsx')
        rules = pd.read_excel(pre_path).fillna('')
        for target in ('pause_phone_groups', 'pause_phones', 'pause_fax_groups', 'pause_faxes',
                       'pause_emails', 'pause_website_groups', 'pause_websites', 'pause_address',
                       'pause_primary_name', 'pause_alias_blob', 'pause_aliases'):
            selected = rules[(rules['source'] == SOURCE) & (rules['target_field'] == target)]
            if len(selected) != 1:
                raise AssertionError(f'Expected exactly one SEC_US_PAUSE rule for {target}.')
        cls.pre = PreNormalizationEngine(rules, pd.DataFrame([
            {'source': SOURCE, 'entity_field': 'entity_type'}]))
        cls.mapper = MappingEngine(load_rules(mapping, SOURCE))
        cls.post = PostNormalizationEngine(pd.read_excel(ROOT / 'data/rules/postNormalization.xlsx'),
                                          {'date_order': 'MDY'})

    def raw(self, text, name='Example Entity', url='https://example.test/alert', notes=''):
        raw = {'entity_type': 'Entity', 'detail': {'name': name, 'contact_text': text,
                                                 'notes': notes},
               'detail_url': url, 'list': {}}
        return PreProcessingEngine().generate_composite_id(raw, {
            'fields': ['detail_url'], 'prefix': 'SEC-US-PAUSE', 'output_field': 'source_record_id'})

    def parse(self, text, **kwargs):
        raw = self.raw(text, **kwargs)
        pre = self.pre.pre_normalize_record(SOURCE, raw)
        final = self.post.post_normalize_record(self.mapper.map_record(pre))
        return pre, final

    def test_16_real_contact_cases_match_explicit_expectations(self):
        self.assertEqual(len(self.fixtures['cases']), 16)
        for case in self.fixtures['cases']:
            with self.subTest(name=case['name']):
                pre, final = self.parse(case['text'], name=case['name'], url=case['url'])
                for field in ('phones', 'faxes', 'emails', 'websites', 'address'):
                    self.assertEqual(pre['pause_' + field], case['expected'][field])
                self.assertEqual(final['EntityId'], case['entity_id'])
                self.assertEqual(final['Countries'], [])
                self.assertEqual(final['Sources'][0]['SourceRecordId'], '')

    def test_10_real_placeholder_addresses_are_empty(self):
        self.assertEqual(len(self.fixtures['placeholder_addresses']), 10)
        for case in self.fixtures['placeholder_addresses']:
            with self.subTest(name=case['name']):
                pre, final = self.parse(case['text'])
                self.assertEqual(pre['pause_address'], '')
                self.assertEqual(final['Addresses'], [])

    def test_multiple_numbers_and_repeated_phone_labels_are_kept(self):
        pre, _ = self.parse('Phone: 123-456-7890, 234-567-8901\nPhone: 345-678-9012')
        self.assertEqual(pre['pause_phones'], ['123-456-7890', '234-567-8901', '345-678-9012'])

    def test_phone_and_fax_are_separate_even_on_one_line(self):
        pre, _ = self.parse('Phone: 123-456-7890 Fax: 234-567-8901 Website: www.example.org')
        self.assertEqual(pre['pause_phones'], ['123-456-7890'])
        self.assertEqual(pre['pause_faxes'], ['234-567-8901'])

    def test_number_labels_and_colonless_tel_are_supported(self):
        for text in ('Phone Number: 123-456-7890 Fax Number: 234-567-8901',
                     'Tel +1 123 456 7890 / Fax +1 234 567 8901'):
            with self.subTest(text=text):
                pre, _ = self.parse(text)
                self.assertEqual(len(pre['pause_phones']), 1)
                self.assertEqual(len(pre['pause_faxes']), 1)
                self.assertEqual(pre['pause_address'], '')

    def test_extensions_stay_with_the_number(self):
        pre, _ = self.parse('Phone: 123-456-7890, ext. 719\nPhone: 234-567-8901 x 42')
        self.assertEqual(pre['pause_phones'], ['123-456-7890, ext. 719', '234-567-8901 x 42'])

    def test_street_numbers_and_zip_codes_do_not_become_phone_numbers(self):
        pre, _ = self.parse('Phone: 123-456-7890\n805 Marion Street Seattle, WA 98104\nWebsite: www.example.org')
        self.assertEqual(pre['pause_phones'], ['123-456-7890'])

    def test_bare_domains_in_website_fields_are_kept(self):
        pre, _ = self.parse('Website: wallet.example.org / example.online')
        self.assertEqual(pre['pause_websites'], ['wallet.example.org', 'example.online'])

    def test_email_domains_and_malformed_emails_are_not_websites(self):
        for text in ('Email: user@www.example.org', 'Email: info.example.org'):
            with self.subTest(text=text):
                pre, _ = self.parse(text)
                self.assertEqual(pre['pause_websites'], [])

    def test_unlabeled_www_after_email_is_detected(self):
        pre, _ = self.parse('Email: user@example.org\nwww.example.org')
        self.assertEqual(pre['pause_emails'], ['user@example.org'])
        self.assertEqual(pre['pause_websites'], ['www.example.org'])

    def test_schemes_paths_and_query_parameters_are_preserved(self):
        pre, _ = self.parse('Website: https://www.example.org/a?x=1&y=2, http://second.example/')
        self.assertEqual(pre['pause_websites'], ['https://www.example.org/a?x=1&y=2', 'http://second.example/'])

    def test_partial_www_is_never_published_as_a_valid_website(self):
        pre, _ = self.parse('Website: http://www.')
        self.assertEqual(pre['pause_websites'], [])

    def test_placeholder_address_filter_does_not_remove_real_road_names(self):
        for value in ('Unknown Road', 'None Such Street', 'No Address Road'):
            with self.subTest(value=value):
                pre, _ = self.parse(value + '\nPhone: 123-456-7890')
                self.assertEqual(pre['pause_address'], value)

    def test_raw_text_notes_and_input_record_are_preserved(self):
        raw = self.raw('No Address Identified\nPhone: 123-456-7890', notes='Source remarks')
        before = copy.deepcopy(raw)
        pre = self.pre.pre_normalize_record(SOURCE, raw)
        self.assertEqual(raw, before)
        self.assertEqual(pre['detail'], before['detail'])

    def test_genuine_firm_notes_do_not_supply_contacts(self):
        pre, final = self.parse('Website: www.claimed.example',
            notes='Genuine firm: Phone: 123-456-7890 Email: real@example.org Website: https://real.example')
        self.assertEqual(pre['pause_phones'], [])
        self.assertEqual(pre['pause_emails'], [])
        self.assertEqual(pre['pause_websites'], ['www.claimed.example'])
        self.assertEqual(final['Identifiers'], [])

    def test_alias_rules_from_the_previous_step_still_apply(self):
        _, final = self.parse('Phone: 123-456-7890', name='Example LLC aka Other LLC')
        self.assertEqual(final['Names'][0]['Name'], 'Example LLC')
        self.assertEqual([a['Alias'] for a in final['Aliases']], ['Other LLC'])

    def test_empty_contacts_make_no_constant_only_objects(self):
        for value in ('', None):
            with self.subTest(value=value):
                _, final = self.parse(value)
                self.assertEqual(final['Contacts'], [])
                self.assertEqual(final['Addresses'], [])


if __name__ == '__main__':
    unittest.main()
