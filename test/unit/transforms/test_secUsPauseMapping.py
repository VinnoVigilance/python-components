"""Offline checks of SEC PAUSE Excel mapping through real transform engines.

SEC_PAUSE_MAPPING_FILE, SEC_PAUSE_RULES_FILE and SEC_PAUSE_EXTRA_RULES_FILE
are optional developer overrides for checking an uninstalled rules bundle.
By default the tests read the installed project workbooks.
"""
import copy
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import yaml
from scrapy.http import HtmlResponse

from ingestion.crawler.models import CrawlerTask
from ingestion.crawler.spiders.genericSpider import GenericSpider
from ingestion.crawler.storage import CrawlerStorage
from transforms.preProcessingEngine import PreProcessingEngine
from transforms.preNormalization import PreNormalizationEngine
from transforms.fieldMapper import MappingEngine, load_rules
from transforms.postNormalization import PostNormalizationEngine

ROOT = Path(__file__).resolve().parents[3]
RULES = ROOT / 'data/rules'
SOURCE = 'SEC_US_PAUSE'
BASE = ('https://www.sec.gov/enforcement-litigation/'
        'public-alerts-unregistered-soliciting-entities')


class TestSecUsPauseMapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapping_path = Path(os.environ.get('SEC_PAUSE_MAPPING_FILE', RULES / 'mapping.xlsx'))
        mapping_df = pd.read_excel(cls.mapping_path)
        if not {SOURCE, SOURCE + ' TYPE'}.issubset(mapping_df.columns):
            raise AssertionError('Copy the two SEC_US_PAUSE mapping columns into mapping.xlsx first.')
        pre_df = pd.read_excel(os.environ.get('SEC_PAUSE_RULES_FILE', RULES / 'preNormalization.xlsx'))
        extra = os.environ.get('SEC_PAUSE_EXTRA_RULES_FILE')
        if extra:
            pre_df = pd.concat([pre_df, pd.read_excel(extra, sheet_name='PreNormalization')],
                               ignore_index=True)
        sec_rules = pre_df.fillna('')
        for target in ('pause_phones', 'pause_faxes', 'pause_emails',
                       'pause_websites', 'pause_address'):
            matching = sec_rules[(sec_rules['source'] == SOURCE)
                                 & (sec_rules['target_field'] == target)]
            if len(matching) != 1:
                raise AssertionError(f'Expected exactly one rule for {target}; found {len(matching)}.')
        cls.pre = PreNormalizationEngine(pre_df, pd.DataFrame([
            {'source': SOURCE, 'entity_field': 'entity_type'}]))
        cls.mapper = MappingEngine(load_rules(cls.mapping_path, SOURCE))
        cls.post = PostNormalizationEngine(pd.read_excel(RULES / 'postNormalization.xlsx'),
                                           {'date_order': 'MDY'})
        cls.preprocessing = [
            {'handler': 'set_constant_field', 'level': 'record',
             'config': {'output_field': 'entity_type', 'value': 'Entity'}},
            {'handler': 'generate_composite_id', 'level': 'record',
             'config': {'fields': ['detail_url'], 'output_field': 'source_record_id',
                        'prefix': 'SEC-US-PAUSE'}},
        ]
        cls.raw = {}
        with TemporaryDirectory() as directory:
            config = yaml.safe_load((ROOT / 'config/watchlistSources/sec_us_pause.yaml')
                                    .read_text(encoding='utf-8'))
            spider = GenericSpider(
                task=CrawlerTask(url=BASE, source_name='TEST-SEC-US', list_name='TEST-PAUSE'),
                crawler_config=config, records=[],
                storage=CrawlerStorage(source_name='TEST-SEC-US', list_name='TEST-PAUSE',
                                       base_dir=directory))
            fixtures = ROOT / 'test/fixtures/watchlist/sec_us_pause'
            listing = HtmlResponse(url=BASE, body=(fixtures / 'listing.html').read_bytes(),
                                   encoding='utf-8')
            requests = {r.url: r for r in spider.parse(listing)
                        if r.callback == spider.parse_detail}
            for slug in ('1st-american-securities', '12-west-capital-management',
                         'anti-cyber-crime-financial'):
                req = requests[BASE + '/' + slug]
                response = HtmlResponse(url=req.url,
                    body=(fixtures / (slug + '.html')).read_bytes(), encoding='utf-8')
                cls.raw[slug] = list(spider.parse_detail(response, **req.cb_kwargs))[0]

    def canonical(self, raw):
        raw = PreProcessingEngine().preprocess([copy.deepcopy(raw)], self.preprocessing)[0]
        normalized = self.pre.pre_normalize_record(SOURCE, raw)
        return self.post.post_normalize_record(self.mapper.map_record(normalized))

    def real(self, slug):
        return self.canonical(self.raw[slug])

    def synthetic(self, text='', **detail):
        return self.canonical({'detail_url': BASE + '/example', 'list': {},
                               'detail': {'name': 'Example', 'contact_text': text, **detail}})

    def test_entity_and_source_metadata(self):
        record = self.real('1st-american-securities')
        self.assertEqual(record['EntityType'], 'Entity')
        source = record['Sources'][0]
        for field, value in {'SourceName': 'SEC-US', 'ListName': SOURCE,
                             'SourceType': 'Official', 'DatasetCategory': 'Regulatory'}.items():
            self.assertEqual(source[field], value)
        self.assertEqual(source['SourceURL'], BASE + '/1st-american-securities')

    def test_generated_id_is_internal_only(self):
        record = self.real('12-west-capital-management')
        self.assertTrue(record['EntityId'].startswith('SEC-US-PAUSE'))
        self.assertFalse(record['Sources'][0]['SourceRecordId'])
        self.assertEqual(record['Identifiers'], [])

    def test_internal_id_is_stable_and_distinguishes_urls(self):
        raw = copy.deepcopy(self.raw['1st-american-securities'])
        first = self.canonical(raw)['EntityId']
        raw['detail']['name'] = 'Corrected spelling'
        self.assertEqual(self.canonical(raw)['EntityId'], first)
        raw['detail_url'] = BASE + '/different-record'
        self.assertNotEqual(self.canonical(raw)['EntityId'], first)

    def test_primary_name_is_the_alert_subject(self):
        record = self.real('12-west-capital-management')
        self.assertEqual(record['Names'][0]['Name'], '12 West Capital Management')
        self.assertEqual(record['Names'][0]['NameType'], 'Primary Name')
        self.assertEqual(record['Aliases'], [])
        self.assertTrue(record['Names'][0]['Normalized_Name'])

    def test_all_three_categories_are_preserved_verbatim(self):
        for slug, category in {
            '1st-american-securities': 'Unregistered Soliciting Entities',
            '12-west-capital-management': 'Impersonators of Genuine Firms',
            'anti-cyber-crime-financial': 'Fictitious Regulators',
        }.items():
            with self.subTest(slug=slug):
                infos = self.real(slug)['additionalInfo']
                self.assertEqual([(i['Type'], i['Value']) for i in infos],
                                 [('Alert Category', category)])

    def test_publication_dates_are_resolved_and_typed(self):
        for slug, original, iso in (
            ('1st-american-securities', 'July 25, 2019', '2019-07-25'),
            ('12-west-capital-management', 'July 19, 2019', '2019-07-19'),
            ('anti-cyber-crime-financial', 'July 18, 2019', '2019-07-18'),
        ):
            with self.subTest(slug=slug):
                record = self.real(slug)
                self.assertEqual(len(record['Dates']), 1)
                date = record['Dates'][0]
                self.assertEqual(date['Type'], 'Alert Date')
                self.assertEqual(date['OriginalValue'], original)
                self.assertEqual(date['FullDate'], iso)
                self.assertFalse(record['DateUpdated'])

    def test_real_address_excludes_phone_and_website(self):
        record = self.real('1st-american-securities')
        self.assertEqual(len(record['Addresses']), 1)
        self.assertEqual(record['Addresses'][0]['FullAddress'],
                         '632 Broadway, Suite 3002 New York, NY 10012')
        self.assertIn('not verified', record['Addresses'][0]['Note'])

    def test_claimed_address_is_not_a_registration_country(self):
        record = self.real('12-west-capital-management')
        self.assertIn('90 Park Avenue', record['Addresses'][0]['FullAddress'])
        self.assertEqual(record['Countries'], [])
        self.assertFalse(record['Addresses'][0]['Country'])

    def test_contacts_have_distinct_types_and_no_placeholders(self):
        contacts = self.real('1st-american-securities')['Contacts']
        self.assertEqual([(c['Type'], c['Value']) for c in contacts], [
            ('Phone', '(646) 313-3785'), ('Fax', '(646) 224-8912'),
            ('Website', 'http://www.firstamericansecurities.com')])
        self.assertTrue(all(c['Note'] for c in contacts))

    def test_multiple_contacts_are_individual_objects(self):
        record = self.synthetic('Phone: 123-456-7890\nPhone: 234-567-8901\nEmail: one@example.org; '
                                'two@example.org\nWebsite: https://example.org')
        self.assertEqual([(c['Type'], c['Value']) for c in record['Contacts']], [
            ('Phone', '123-456-7890'), ('Phone', '234-567-8901'), ('Email', 'one@example.org'),
            ('Email', 'two@example.org'), ('Website', 'https://example.org')])
        self.assertEqual(record['Addresses'], [])

    def test_real_bare_website_is_preserved(self):
        record = self.real('anti-cyber-crime-financial')
        self.assertEqual([(c['Type'], c['Value']) for c in record['Contacts']],
                         [('Email', 'antifraud@accffa.org'), ('Website', 'www.accffa.org')])

    def test_genuine_firm_data_is_only_narrative(self):
        record = self.real('12-west-capital-management')
        remarks = [c['text'] for c in record['Comments'] if c['type'] == 'Remarks']
        self.assertEqual(len(remarks), 1)
        for value in ('12 West Capital Management LP', '160197', '801-73235', '0001540531'):
            self.assertIn(value, remarks[0])
            self.assertNotIn(value, str(record['Identifiers']))
            self.assertNotIn(value, str(record['Contacts']))
        self.assertEqual(record['Relationships'], [])

    def test_raw_contact_text_is_retained_in_comments(self):
        raw = self.raw['1st-american-securities']
        record = self.canonical(raw)
        comments = [c['text'] for c in record['Comments'] if c['type'] == 'Additional Info']
        # The project's existing final cleanup folds whitespace in strings.
        self.assertEqual(comments, [' '.join(raw['detail']['contact_text'].split())])

    def test_empty_optional_fields_do_not_make_constant_only_objects(self):
        record = self.synthetic('')
        for field in ('Dates', 'Addresses', 'Contacts', 'Comments', 'additionalInfo'):
            self.assertEqual(record[field], [])

    def test_address_only_and_placeholder_inputs(self):
        address_only = self.synthetic('123 Example Road\nExample City')
        self.assertEqual(address_only['Addresses'][0]['FullAddress'],
                         '123 Example Road Example City')
        for text in ('None', 'N/A', 'Unknown', 'Phone: None\nFax: None'):
            with self.subTest(text=text):
                record = self.synthetic(text)
                self.assertEqual(record['Addresses'], [])
                self.assertEqual(record['Contacts'], [])

    def test_no_inferred_sanctions_or_legal_findings(self):
        for slug in self.raw:
            record = self.real(slug)
            self.assertEqual(record['Measures'], [])
            self.assertEqual(record['Programs'], [])
            self.assertIn('not a determination', record['Sources'][0]['Note'])

    def test_existing_and_new_enum_values_are_registered(self):
        picks = pd.read_excel(RULES / 'pickLists.xlsx').fillna('')
        extra = os.environ.get('SEC_PAUSE_EXTRA_RULES_FILE')
        if extra:
            picks = pd.concat([picks, pd.read_excel(extra, sheet_name='PickLists')],
                              ignore_index=True)
        pairs = {(str(r['Path']), str(r['Value'])) for _, r in picks.iterrows()}
        for pair in [('Dates[].Type', 'Alert Date'), ('additionalInfo.Type', 'Alert Category'),
                     ('Names.NameType', 'Primary Name'), ('Sources.SourceType', 'Official'),
                     ('Sources.DatasetCategory', 'Regulatory'),
                     *[('Contacts.Type', kind) for kind in ('Phone', 'Fax', 'Email', 'Website')]]:
            self.assertIn(pair, pairs)


if __name__ == '__main__':
    unittest.main()
