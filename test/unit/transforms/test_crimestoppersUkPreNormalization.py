"""Offline tests against the rules actually installed in the project's XLSX.

CS_RULES_WORKBOOK optionally points to the supplied two-sheet rules workbook.
"""
import copy
import os
import unittest
from pathlib import Path

import pandas as pd

from transforms.preNormalization import FirstNonEmptyHandler, PreNormalizationEngine

ROOT = Path(__file__).resolve().parents[3]
SOURCE = 'CRIMESTOPPERS_UK_MOST_WANTED'
TARGETS = ['cs_name_before_wanted', 'cs_name_after_police', 'cs_primary_name', 'cs_aliases']


class TestFirstNonEmptyHandler(unittest.TestCase):
    def setUp(self):
        self.handler = FirstNonEmptyHandler()

    def test_first_populated_path_wins(self):
        self.assertEqual(self.handler.normalize({'a':'First','b':'Second'}, 'a|b'), 'First')

    def test_skips_missing_none_and_whitespace(self):
        self.assertEqual(self.handler.normalize({'a':None,'b':'  ','c':'Value'}, 'missing|a|b|c'), 'Value')

    def test_nested_paths(self):
        self.assertEqual(self.handler.normalize({'a':{'name':'Nested'}}, 'a.name|b'), 'Nested')

    def test_array_paths(self):
        self.assertEqual(self.handler.normalize({'a':[{'v':''},{'v':'Second'}]}, 'a[].v'), 'Second')

    def test_false_and_zero_are_kept(self):
        for value in [False, 0]:
            with self.subTest(value=value):
                self.assertIs(self.handler.normalize({'a':value,'b':'Fallback'}, 'a|b'), value)

    def test_empty_containers_are_skipped(self):
        self.assertEqual(self.handler.normalize({'a':[],'b':{},'c':'Value'}, 'a|b|c'), 'Value')

    def test_placeholder_cleanup_is_not_duplicated(self):
        for value in ['N/A','None','UNKNOWN','NA']:
            with self.subTest(value=value):
                self.assertEqual(self.handler.normalize({'a':value,'b':'Fallback'}, 'a|b'), value)

    def test_no_candidates_is_blank(self):
        self.assertEqual(self.handler.normalize({}, 'a|b'), '')

    def test_non_object_is_safe(self):
        self.assertEqual(self.handler.normalize(None, 'a'), '')

    def test_empty_rule_is_rejected(self):
        with self.assertRaises(ValueError):
            self.handler.normalize({}, ' | ')

    def test_whole_record_cannot_be_overwritten(self):
        rules=pd.DataFrame([{'source':'TEST','entity_type':'*','field':'$',
            'normalization_type':'first_non_empty','normalization_rule':'name','target_field':''}])
        engine=PreNormalizationEngine(rules,pd.DataFrame(columns=['source','entity_field']))
        with self.assertRaises(ValueError):
            engine.pre_normalize_record('TEST',{'name':'Example'})


class TestCrimestoppersUkPreNormalization(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        standalone = os.environ.get('CS_RULES_WORKBOOK')
        if standalone:
            rule_df = pd.read_excel(standalone, sheet_name='preNormalization', keep_default_na=False)
            sources = pd.read_excel(standalone, sheet_name='sourceConfig', keep_default_na=False)
        else:
            rule_df = pd.read_excel(ROOT / 'data/rules/preNormalization.xlsx', keep_default_na=False)
            sources = pd.read_excel(ROOT / 'data/rules/sourceConfig.xlsx', keep_default_na=False)
        extra = os.environ.get('CS_MAPPING_EXTRA_RULES')
        if extra:
            rule_df = pd.concat([rule_df, pd.read_excel(extra, sheet_name='PreNormalization', keep_default_na=False)], ignore_index=True)
        rule_df = rule_df.fillna('')
        cls.rules = rule_df[rule_df['source'] == SOURCE]
        targets = [target for target in cls.rules['target_field'].tolist() if target in TARGETS]
        if targets != TARGETS:
            raise AssertionError('Copy preNormalization!A2:F5 in order, without duplicate rows.')
        source_rows = sources[sources['source'] == SOURCE]
        if len(source_rows) != 1 or source_rows.iloc[0]['entity_field'] != 'entity_type':
            raise AssertionError('Add the sourceConfig!A2:C2 row exactly once.')
        cls.engine = PreNormalizationEngine(cls.rules, source_rows)

    def normalize(self, name='', title='', nickname='', **extras):
        return self.engine.pre_normalize_record(SOURCE, {
            'entity_type':'Individual', 'source_record_id':'501',
            'detail_url':'https://example.test/appeal?ID=501',
            'detail':{'suspect_name':name,'page_title':title,'nickname':nickname,**extras},
        })

    def test_structured_name_has_priority(self):
        r=self.normalize('Actual Person', 'Other Person - Wanted for test')
        self.assertEqual(r['cs_primary_name'], 'Actual Person')

    def test_name_before_wanted_title(self):
        r=self.normalize(title='Example PERSON - Wanted for test')
        self.assertEqual(r['cs_primary_name'], 'Example PERSON')
        self.assertEqual(r['detail']['suspect_name'], '')

    def test_name_after_police_title(self):
        self.assertEqual(self.normalize(title='Wanted by Example Police - Sample PERSON')['cs_primary_name'], 'Sample PERSON')

    def test_unknown_identity_is_not_a_person_name(self):
        self.assertEqual(self.normalize(title='Identity required')['cs_primary_name'], '')

    def test_generic_crime_headline_is_not_a_person_name(self):
        self.assertEqual(self.normalize(title='Wanted for Murder')['cs_primary_name'], '')

    def test_name_punctuation_is_preserved(self):
        self.assertEqual(self.normalize(title="Anne O'EXAMPLE-SMITH - Wanted for test")['cs_primary_name'], "Anne O'EXAMPLE-SMITH")

    def test_comma_semicolon_and_conjunction_aliases(self):
        self.assertEqual(self.normalize(nickname='First Person, Second Person; Third Person and Fourth Person')['cs_aliases'], ['First Person','Second Person','Third Person','Fourth Person'])

    def test_slashes_split_aliases_without_inventing_surnames(self):
        self.assertEqual(self.normalize(nickname='Ollie/Olly EXAMPLE')['cs_aliases'], ['Ollie','Olly EXAMPLE'])

    def test_uppercase_surname_period_separator(self):
        self.assertEqual(self.normalize(nickname='Thomas EXAMPLE. Thomas SAMPLE')['cs_aliases'], ['Thomas EXAMPLE','Thomas SAMPLE'])

    def test_honorific_period_is_kept(self):
        self.assertEqual(self.normalize(nickname='Dr Ryan Example')['cs_aliases'], ['Dr Ryan Example'])
        self.assertEqual(self.normalize(nickname='Dr. Ryan Example')['cs_aliases'], ['Dr. Ryan Example'])

    def test_na_is_not_split_into_two_fake_aliases(self):
        self.assertEqual(self.normalize(nickname='N/A')['cs_aliases'], ['N/A'])

    def test_none_string_is_left_for_existing_mapping_cleanup(self):
        self.assertEqual(self.normalize(nickname='None')['cs_aliases'], ['None'])

    def test_existing_mapper_drops_placeholder_aliases(self):
        from transforms.fieldMapper import MappingEngine, Rule
        mapper=MappingEngine([
            Rule('Individual','Aliases[].Alias','string','aliases','list_expand','cs_aliases[]'),
            Rule('Individual','Aliases[].AliasType','string','aliases','constant','AKA'),
        ])
        for value in ['N/A','None']:
            with self.subTest(value=value):
                result=mapper.map_record(self.normalize(nickname=value))
                self.assertEqual(result['Aliases'], [])

    def test_empty_aliases_are_empty_list(self):
        for value in ['', None]:
            self.assertEqual(self.normalize(nickname=value)['cs_aliases'], [])

    def test_ambiguous_unseparated_aliases_are_preserved(self):
        self.assertEqual(self.normalize(nickname='Hollie Wyn Example Julia Example')['cs_aliases'], ['Hollie Wyn Example Julia Example'])

    def test_raw_record_and_ids_are_preserved(self):
        raw={'entity_type':'Individual','source_record_id':'501','detail_url':'https://example.test/appeal?ID=501','detail':{'suspect_name':'','page_title':'Example PERSON - Wanted for test','nickname':'N/A','age_raw':'40 - 45','height_raw':'175 - 180 cm','crime_location':'Example location','ethnic_appearance':'Example appearance','full_details':'Call 0800 555 111'}}
        before=copy.deepcopy(raw)
        result=self.engine.pre_normalize_record(SOURCE,raw)
        self.assertEqual(raw,before)
        self.assertEqual(result['detail'],before['detail'])
        self.assertEqual(result['source_record_id'],'501')
        self.assertEqual(result['detail_url'],before['detail_url'])
        for forbidden in ['Dates','Countries','Contacts','Addresses']:
            self.assertNotIn(forbidden,result)

    def test_other_source_is_unchanged(self):
        raw={'entity_type':'Individual','detail':{'page_title':'Example - Wanted for test'}}
        self.assertEqual(self.engine.pre_normalize_record('OTHER',raw),raw)


if __name__ == '__main__':
    unittest.main()
