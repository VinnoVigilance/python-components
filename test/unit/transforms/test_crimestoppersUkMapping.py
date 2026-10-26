"""Offline mapping QA against installed XLSX and the real transform engines.

Developer overrides: CS_MAPPING_FILE, CS_PRENORM_WORKBOOK,
CS_MAPPING_EXTRA_RULES. No network, DB or object-storage access is made.
"""
import copy
import os
import unittest
from pathlib import Path

import pandas as pd

from transforms.preNormalization import PreNormalizationEngine
from transforms.fieldMapper import MappingEngine, load_rules
from transforms.postNormalization import PostNormalizationEngine

ROOT=Path(__file__).resolve().parents[3]
RULES=ROOT/'data/rules'
SOURCE='CRIMESTOPPERS_UK_MOST_WANTED'
BASE='https://crimestoppers-uk.org/give-information/most-wanted/appeal?ID='


class TestCrimestoppersUkMapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path(os.environ.get('CS_MAPPING_FILE',RULES/'mapping.xlsx'))
        frame=pd.read_excel(path)
        if not {SOURCE,SOURCE+' TYPE'}.issubset(frame.columns):
            raise AssertionError('Copy Mapping!F1:G429 into two new aligned columns of mapping.xlsx.')
        standalone=os.environ.get('CS_PRENORM_WORKBOOK')
        if standalone:
            pre=pd.read_excel(standalone,sheet_name='preNormalization')
        else:
            pre=pd.read_excel(RULES/'preNormalization.xlsx')
        extra=os.environ.get('CS_MAPPING_EXTRA_RULES')
        if extra:
            pre=pd.concat([pre,pd.read_excel(extra,sheet_name='PreNormalization')],ignore_index=True)
        pre=pre.fillna('')
        for target in ['cs_name_before_wanted','cs_name_after_police','cs_primary_name','cs_aliases','cs_facial_hair']:
            n=len(pre[(pre['source']==SOURCE)&(pre['target_field']==target)])
            if n!=1:raise AssertionError(f'Expected one rule for {target}; found {n}.')
        cls.pre=PreNormalizationEngine(pre,pd.DataFrame([{'source':SOURCE,'entity_field':'entity_type'}]))
        cls.mapper=MappingEngine(load_rules(path,SOURCE))
        cls.post=PostNormalizationEngine(pd.read_excel(RULES/'postNormalization.xlsx'),{'date_order':'DMY'})

    def raw(self,**detail):
        values={
            'page_title':'Example PERSON - Wanted for test',
            'suspect_name':'Example PERSON','nickname':'Example AKA','sex':'Male',
            'cs_reference':'CS-EXAMPLE-1','police_force':'Example Police',
            'crime_type':'Example alleged offence','crime_location':'Example incident location',
            'whereabouts':'Location unknown; has links to an example area',
            'age_raw':'40 - 45','height_raw':'175 - 180 cm (approx 5 foot 10)',
            'build':'Medium','hair_colour':'Black','hair_type':'Straight',
            'facial_hair':'Beard','ethnic_appearance':'Example appearance',
            'people_involved':'Lone offender','eye_colour':'',
            'summary':'Example summary','full_details':'Example appeal description',
            'additional_information':'Example extra information',
        }
        values.update(detail)
        return {'entity_type':'Individual','source_record_id':'501','detail_url':BASE+'501',
            'list':{'whereabouts':'Example listing location'},'detail':values,
            'attachments':[{'type':'Profile','url':BASE+'501'},
                           {'type':'Photograph','url':'https://example.test/person.jpg'}]}

    def canonical(self,raw):
        return self.post.post_normalize_record(self.mapper.map_record(self.pre.pre_normalize_record(SOURCE,raw)))

    def info(self,record):
        return {r['Type']:r['Value'] for r in record['additionalInfo']}

    def test_entity_and_gender(self):
        r=self.canonical(self.raw())
        self.assertEqual(r['EntityType'],'Individual')
        self.assertEqual(r['Gender'],'Male')

    def test_source_metadata(self):
        s=self.canonical(self.raw())['Sources'][0]
        for field,value in {'SourceType':'NGO','DatasetCategory':'Law Enforcement',
                            'SourceName':'CRIMESTOPPERS-UK','ListName':SOURCE}.items():
            self.assertEqual(s[field],value)

    def test_source_uses_detail_url_and_real_appeal_id(self):
        r=self.canonical(self.raw())
        self.assertEqual(r['EntityId'],'501')
        self.assertEqual(r['Sources'][0]['SourceRecordId'],'501')
        self.assertEqual(r['Sources'][0]['SourceURL'],BASE+'501')

    def test_id_is_stable_when_name_changes(self):
        r=self.raw();first=self.canonical(r)['EntityId'];r['detail']['suspect_name']='Corrected name'
        self.assertEqual(self.canonical(r)['EntityId'],first)

    def test_different_appeals_are_not_merged_by_case_reference(self):
        a=self.raw();b=copy.deepcopy(a);b['source_record_id']='502';b['detail_url']=BASE+'502'
        self.assertNotEqual(self.canonical(a)['EntityId'],self.canonical(b)['EntityId'])

    def test_source_note_preserves_police_reference_and_title(self):
        note=self.canonical(self.raw())['Sources'][0]['Note']
        for part in ['Example Police','CS-EXAMPLE-1','Example PERSON - Wanted for test']:
            self.assertIn(part,note)

    def test_primary_name_and_search_enrichment(self):
        r=self.canonical(self.raw());name=r['Names'][0]
        self.assertEqual(name['Name'],'Example PERSON')
        self.assertEqual(name['NameType'],'Primary Name')
        self.assertTrue(name['Normalized_Name'])
        self.assertTrue(name['Search_Tokens'])

    def test_title_name_fallback(self):
        r=self.canonical(self.raw(suspect_name='',page_title='Example SECOND - Wanted for test'))
        self.assertEqual(r['Names'][0]['Name'],'Example SECOND')

    def test_unknown_identity_is_preserved_without_fake_name(self):
        raw=self.raw(suspect_name='',page_title='Identity required',nickname='N/A')
        r=self.canonical(raw)
        self.assertEqual(r['EntityId'],'501')
        self.assertEqual(r['Names'],[])
        self.assertEqual(r['Aliases'],[])
        self.assertIn('Identity required',r['Sources'][0]['Note'])
        self.assertTrue(r['Attachments'])

    def test_aliases_are_split(self):
        r=self.canonical(self.raw(nickname='First Person, Second Person and Third Person'))
        self.assertEqual([a['Alias'] for a in r['Aliases']],['First Person','Second Person','Third Person'])
        self.assertTrue(all(a['AliasType']=='AKA' for a in r['Aliases']))

    def test_placeholder_aliases_do_not_become_people(self):
        for value in ['N/A','None','']:
            self.assertEqual(self.canonical(self.raw(nickname=value))['Aliases'],[])

    def test_duplicate_aliases_are_removed_by_existing_postnormalization(self):
        r=self.canonical(self.raw(nickname='Same Person, Same Person'))
        self.assertEqual([a['Alias'] for a in r['Aliases']],['Same Person'])

    def test_ranges_are_kept_verbatim(self):
        info=self.info(self.canonical(self.raw()))
        self.assertEqual(info['Age Range'],'40 - 45')
        self.assertEqual(info['Height'],'175 - 180 cm (approx 5 foot 10)')

    def test_age_is_not_turned_into_birth_date(self):
        r=self.canonical(self.raw())
        self.assertEqual(r['Dates'],[])
        self.assertFalse(r['DateAdded']);self.assertFalse(r['DateUpdated'])

    def test_ethnic_appearance_is_not_a_nationality(self):
        r=self.canonical(self.raw())
        self.assertEqual(r['Countries'],[])
        self.assertEqual(self.info(r)['Ethnic Appearance'],'Example appearance')

    def test_crime_location_is_not_a_residential_address(self):
        r=self.canonical(self.raw())
        self.assertEqual(r['Addresses'],[]);self.assertEqual(r['Places'],[])
        self.assertEqual(self.info(r)['Crime Location'],'Example incident location')

    def test_listing_and_detail_whereabouts_are_distinct(self):
        info=self.info(self.canonical(self.raw()))
        self.assertEqual(info['Listing Whereabouts'],'Example listing location')
        self.assertEqual(info['Whereabouts'],'Location unknown; has links to an example area')

    def test_case_reference_is_not_a_personal_identity_document(self):
        r=self.canonical(self.raw())
        self.assertEqual(r['Identifiers'],[])
        self.assertEqual(self.info(r)['National Reference'],'CS-EXAMPLE-1')

    def test_offence_is_preserved_without_assumed_conviction(self):
        r=self.canonical(self.raw())
        self.assertEqual(self.info(r)['Offense'],'Example alleged offence')
        self.assertEqual(r['Measures'],[]);self.assertEqual(r['Programs'],[])

    def test_body_sections_have_separate_comment_types(self):
        r=self.canonical(self.raw())
        self.assertEqual([(c['type'],c['text']) for c in r['Comments']],
            [('Summary','Example summary'),('Remarks','Example appeal description'),('Additional Info','Example extra information')])

    def test_missing_additional_information_does_not_create_empty_comment(self):
        r=self.canonical(self.raw(additional_information=''))
        self.assertEqual(len(r['Comments']),2)

    def test_reporting_phone_is_not_a_person_contact(self):
        r=self.canonical(self.raw(full_details='Please call Crimestoppers on 0800 555 111'))
        self.assertEqual(r['Contacts'],[])
        self.assertIn('0800 555 111',r['Comments'][1]['text'])

    def test_photograph_and_profile_links(self):
        r=self.canonical(self.raw())
        self.assertEqual([(a['Type'],a['URL']) for a in r['Attachments']],
            [('Profile',BASE+'501'),('Photograph','https://example.test/person.jpg')])

    def test_duplicate_photograph_links_are_removed(self):
        raw=self.raw();raw['attachments'].append(copy.deepcopy(raw['attachments'][1]))
        self.assertEqual(len(self.canonical(raw)['Attachments']),2)

    def test_no_photograph_does_not_drop_the_appeal(self):
        raw=self.raw();raw['attachments']=raw['attachments'][:1]
        r=self.canonical(raw);self.assertEqual(r['EntityId'],'501');self.assertEqual(len(r['Attachments']),1)

    def test_facial_hair_none_is_preserved_as_a_reported_value(self):
        raw=self.raw(facial_hair='None');r=self.canonical(raw)
        self.assertEqual(self.info(r)['Facial Hair'],'None (as reported)')
        self.assertEqual(raw['detail']['facial_hair'],'None')

    def test_empty_facial_hair_has_no_attribute(self):
        self.assertNotIn('Facial Hair',self.info(self.canonical(self.raw(facial_hair=''))))

    def test_original_input_is_unchanged(self):
        raw=self.raw();before=copy.deepcopy(raw);self.canonical(raw)
        self.assertEqual(raw,before)


if __name__=='__main__':
    unittest.main()
