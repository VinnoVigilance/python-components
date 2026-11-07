"""Exercise the project's Excel rules through its real normalization engine.

No HTTP, database, browser or storage-service access is made.
SEC_PAUSE_RULES_FILE is an optional path for testing the standalone rules sheet.
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
from transforms.preNormalization import PreNormalizationEngine


ROOT = Path(__file__).resolve().parents[3]
BASE = ('https://www.sec.gov/enforcement-litigation/'
        'public-alerts-unregistered-soliciting-entities')
TARGETS = ('pause_phones', 'pause_faxes', 'pause_emails', 'pause_websites')


class TestSecUsPausePreNormalization(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rules_path = Path(os.environ.get(
            'SEC_PAUSE_RULES_FILE', ROOT / 'data/rules/preNormalization.xlsx'))
        all_rules = pd.read_excel(rules_path).fillna('')
        cls.rules = all_rules[all_rules['source'] == 'SEC_US_PAUSE']
        for target in TARGETS:
            if len(cls.rules[cls.rules['target_field'] == target]) != 1:
                raise AssertionError(
                    f'Expected exactly one SEC_US_PAUSE rule for {target}; '
                    'copy A2:F5 from the supplied workbook into preNormalization.xlsx.')
        cls.engine = PreNormalizationEngine(
            cls.rules,
            pd.DataFrame([{'source': 'SEC_US_PAUSE', 'entity_field': 'entity_type'}]),
        )
        cls.real_records = {}
        with TemporaryDirectory() as directory:
            config = yaml.safe_load((ROOT / 'config/watchlistSources/sec_us_pause.yaml')
                                    .read_text(encoding='utf-8'))
            storage = CrawlerStorage(source_name='TEST-SEC-US', list_name='TEST-PAUSE',
                                     base_dir=directory)
            spider = GenericSpider(task=CrawlerTask(
                url=BASE, source_name='TEST-SEC-US', list_name='TEST-PAUSE'),
                crawler_config=config, storage=storage, records=[])
            fixtures = ROOT / 'test/fixtures/watchlist/sec_us_pause'
            listing = HtmlResponse(url=BASE, body=(fixtures / 'listing.html').read_bytes(),
                                   encoding='utf-8')
            requests = {r.url: r for r in spider.parse(listing)
                        if r.callback == spider.parse_detail}
            for slug in ('1st-american-securities', '12-west-capital-management',
                         'anti-cyber-crime-financial'):
                request = requests[BASE + '/' + slug]
                response = HtmlResponse(url=request.url,
                    body=(fixtures / (slug + '.html')).read_bytes(), encoding='utf-8')
                record = list(spider.parse_detail(response, **request.cb_kwargs))[0]
                record['entity_type'] = 'Entity'
                cls.real_records[slug] = record

    def normalize(self, text='', **detail):
        return self.engine.pre_normalize_record('SEC_US_PAUSE', {
            'entity_type': 'Entity', 'detail': {'contact_text': text, **detail}})

    def real(self, slug):
        return self.engine.pre_normalize_record('SEC_US_PAUSE', self.real_records[slug])

    def test_real_phone_and_fax_are_separate(self):
        result = self.real('1st-american-securities')
        self.assertEqual(result['pause_phones'], ['(646) 313-3785'])
        self.assertEqual(result['pause_faxes'], ['(646) 224-8912'])

    def test_real_http_website_is_kept_without_rewriting(self):
        self.assertEqual(self.real('1st-american-securities')['pause_websites'],
                         ['http://www.firstamericansecurities.com'])

    def test_real_bare_website_and_email(self):
        result = self.real('anti-cyber-crime-financial')
        self.assertEqual(result['pause_emails'], ['antifraud@accffa.org'])
        self.assertEqual(result['pause_websites'], ['www.accffa.org'])
        self.assertEqual(result['pause_phones'], [])
        self.assertEqual(result['pause_faxes'], [])

    def test_genuine_firm_notes_cannot_supply_contacts(self):
        result = self.normalize('Phone: None\nWebsite: www.claimed.example',
            notes='Genuine firm: Phone: +1 212 555 1234; contact@real.example; '
                  'https://real.example CRD 160197 SEC 801-73235 CIK 0001540531')
        self.assertEqual(result['pause_phones'], [])
        self.assertEqual(result['pause_emails'], [])
        self.assertEqual(result['pause_websites'], ['www.claimed.example'])
        self.assertNotIn('Identifiers', result)

    def test_real_impersonator_uses_only_contact_block(self):
        result = self.real('12-west-capital-management')
        self.assertEqual(result['pause_websites'], ['http://www.12-wcm.com'])
        self.assertEqual(result['pause_phones'], [])
        for identifier in ('160197', '801-73235', '0001540531'):
            self.assertIn(identifier, result['detail']['notes'])
            self.assertNotIn(identifier, str([result.get(k) for k in TARGETS]))

    def test_missing_contact_field_is_safe(self):
        result = self.engine.pre_normalize_record('SEC_US_PAUSE',
                    {'entity_type': 'Entity', 'detail': {}})
        for target in TARGETS:
            self.assertFalse(result.get(target))

    def test_empty_and_none_contact_are_safe(self):
        for value in ('', None):
            with self.subTest(value=value):
                result = self.normalize(value)
                for target in TARGETS:
                    self.assertEqual(result[target], [])

    def test_phone_labels_case_and_crlf(self):
        result = self.normalize('  telephone: +44 20 1234 5678\r\n'
                                '\tTEL.: (212) 555-0100\r\nFAX: +44 20 1234 5679')
        self.assertEqual(result['pause_phones'], ['+44 20 1234 5678', '(212) 555-0100'])
        self.assertEqual(result['pause_faxes'], ['+44 20 1234 5679'])

    def test_placeholder_values_do_not_become_contacts(self):
        result = self.normalize('Phone: None\nTelephone: N/A\nTel: Unknown\n'
                                'Fax: None\nEmail: None\nWebsite: N/A')
        for target in TARGETS:
            self.assertEqual(result[target], [])

    def test_address_digits_are_not_a_phone(self):
        self.assertEqual(self.normalize('632 Broadway, Suite 3002\n'
                                       'New York, NY 10012')['pause_phones'], [])

    def test_multiple_emails_and_websites_are_preserved(self):
        result = self.normalize('Email: first@example.org; second+tag@example.org\n'
             'Website: https://example.org/a?x=1&y=2; www.second.example.')
        self.assertEqual(result['pause_emails'],
                         ['first@example.org', 'second+tag@example.org'])
        self.assertEqual(result['pause_websites'],
                         ['https://example.org/a?x=1&y=2', 'www.second.example'])

    def test_email_domain_is_not_extracted_as_a_website(self):
        result = self.normalize('Email: user@www.example.org')
        self.assertEqual(result['pause_emails'], ['user@www.example.org'])
        self.assertEqual(result['pause_websites'], [])

    def test_raw_fields_and_input_are_preserved(self):
        raw = copy.deepcopy(self.real_records['12-west-capital-management'])
        before = copy.deepcopy(raw)
        result = self.engine.pre_normalize_record('SEC_US_PAUSE', raw)
        self.assertEqual(raw, before)
        self.assertEqual(result['detail'], before['detail'])
        self.assertEqual(result['list'], before['list'])

    def test_other_source_does_not_receive_pause_rules(self):
        raw = {'entity_type': 'Entity', 'detail': {'contact_text': 'Phone: 12345'}}
        result = self.engine.pre_normalize_record('OTHER_SOURCE', raw)
        self.assertEqual(result, raw)


if __name__ == '__main__':
    unittest.main()
