"""Extraction contracts using synthetic fixtures shaped like the observed HTML."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml
from scrapy.http import HtmlResponse

from ingestion.crawler.models import CrawlerTask
from ingestion.crawler.pagination import PaginationError
from ingestion.crawler.spiders.genericSpider import GenericSpider
from ingestion.crawler.storage import CrawlerStorage


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / 'test/fixtures/watchlist/crimestoppers_uk_most_wanted'
BASE = 'https://crimestoppers-uk.org/give-information/most-wanted'


class TestCrimestoppersUkExtraction(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = yaml.safe_load((ROOT / 'config/watchlistSources/crimestoppers_uk_most_wanted.yaml')
                                     .read_text(encoding='utf-8'))
        self.storage = CrawlerStorage('TEST', 'CRIMESTOPPERS-TEST', self.directory.name)
        self.records = []
        self.spider = GenericSpider(task=CrawlerTask(url=BASE, source_name='TEST',
            list_name='CRIMESTOPPERS-TEST'), crawler_config=self.config,
            storage=self.storage, records=self.records)
        self.first_requests = list(self.spider.parse(self.page('listing_first.html', BASE)))
        self.details = {r.cb_kwargs['record_id']: r for r in self.first_requests
                        if r.callback == self.spider.parse_detail}

    def page(self, filename, url):
        return HtmlResponse(url, body=(FIXTURES / filename).read_bytes(), encoding='utf-8')

    def extract(self, record_id='501', filename='profile_named.html'):
        request = self.details[record_id]
        return list(self.spider.parse_detail(self.page(filename, request.url),
                                            **request.cb_kwargs))[0]

    def test_config_uses_native_scrapy_and_a_full_listing_guard(self):
        self.assertEqual(self.config['fetch_strategy'], 'direct')
        self.assertEqual(self.config['crawler_settings'].get('http_client', 'scrapy'), 'scrapy')
        self.assertEqual(self.config['pagination']['next_link_attribute'], 'value')
        self.assertEqual(self.config['pagination']['next_link_query_param'], 'page')
        self.assertTrue(self.config['pagination']['required_page_selector'])

    def test_gallery_discovers_each_profile_once_and_ignores_outside_links(self):
        self.assertEqual(set(self.details), {'501', '502'})
        self.assertEqual(len(self.first_requests), 3)

    def test_next_page_is_built_from_numeric_value(self):
        requests = [r for r in self.first_requests if r.callback == self.spider.parse]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url, BASE + '?page=2')
        self.assertEqual(requests[0].method, 'GET')

    def test_last_page_completes_and_preserves_combined_listing(self):
        requests = list(self.spider.parse(self.page('listing_last.html', BASE + '?page=2')))
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].cb_kwargs['record_id'], '503')
        self.assertTrue(self.spider.pagination_state['complete'])
        saved = self.storage.source_file_path.read_text(encoding='utf-8')
        self.assertIn('data-listing-url=', saved)
        for identity in ('501', '502', '503'):
            self.assertIn('ID=' + identity, saved)

    def test_id_comes_from_source_url_and_keeps_query_parameters(self):
        for suffix in ('?ID=501', '?region=x&ID=501&other=y', '?ID=501#top'):
            self.assertEqual(self.spider._extract_record_id(BASE + '/appeal' + suffix, {}), '501')
        self.assertIsNone(self.spider._extract_record_id(BASE + '/appeal?ID=bad', {}))
        self.assertIsNone(self.spider._extract_record_id(BASE + '/appeal', {}))

    def test_actual_suspect_name_is_separate_from_appeal_headline(self):
        record = self.extract()
        self.assertEqual(record['detail']['suspect_name'], 'Example Person')
        self.assertEqual(record['detail']['page_title'], 'Wanted for a test offence')
        self.assertEqual(record['list']['headline'], 'Appeal headline 501')
        self.assertNotIn('Wrong header name', record['detail']['suspect_name'])

    def test_reference_and_police_force_are_extracted_from_profile(self):
        detail = self.extract()['detail']
        self.assertEqual(detail['cs_reference'], 'CS0000-TEST')
        self.assertEqual(detail['police_force'], 'Example Force')
        self.assertEqual(detail['crime_type'], 'Test offence')
        self.assertEqual(detail['people_involved'], '1')

    def test_nickname_and_placeholder_values_are_raw_at_extraction(self):
        self.assertEqual(self.extract()['detail']['nickname'], 'Sample Alias')
        unknown = self.extract('502', 'profile_unknown.html')
        self.assertEqual(unknown['detail']['nickname'], 'N/A')
        self.assertEqual(self.extract()['detail']['whereabouts'], 'N/A')

    def test_crime_location_is_separate_from_whereabouts(self):
        detail = self.extract()['detail']
        self.assertEqual(detail['crime_location'], 'Test incident location')
        self.assertEqual(detail['whereabouts'], 'N/A')
        self.assertNotIn('address', detail)

    def test_age_height_and_appearance_remain_source_reported(self):
        detail = self.extract()['detail']
        self.assertEqual(detail['age_raw'], '40 - 45')
        self.assertEqual(detail['height_raw'], '175 - 180 cm (approx 5 ft 10 in)')
        self.assertEqual(detail['ethnic_appearance'], 'Source-reported appearance')
        self.assertNotIn('date_of_birth', detail)
        self.assertNotIn('nationality', detail)

    def test_sections_preserve_nested_text_without_leaking_other_sections(self):
        detail = self.extract()['detail']
        self.assertEqual(detail['summary'], 'Summary text. Nested summary text.')
        self.assertEqual(detail['full_details'], 'Full details text. Nested full details.')
        self.assertEqual(detail['additional_information'], 'Additional test information.')
        for field in ('summary', 'full_details', 'additional_information'):
            self.assertNotIn('0800 555 111', detail[field])
            self.assertNotIn('unwanted script', detail[field])

    def test_unknown_names_are_not_replaced_by_a_crime_headline(self):
        record = self.extract('502', 'profile_unknown.html')
        self.assertEqual(record['detail']['suspect_name'], 'Unknown')
        self.assertNotEqual(record['detail']['suspect_name'], record['detail']['page_title'])

    def test_missing_optional_fields_do_not_capture_other_text(self):
        detail = self.extract('502', 'profile_minimal.html')['detail']
        self.assertEqual(detail['suspect_name'], '')
        self.assertEqual(detail['age_raw'], '')
        self.assertEqual(detail['sex'], '')
        self.assertEqual(detail['photo_urls'], [])
        self.assertEqual(detail['summary'], '')
        self.assertEqual(detail['description_attributes'], [])
        self.assertEqual(detail['full_details'], 'Minimal details.')

    def test_additional_labelled_attributes_are_retained(self):
        detail = self.extract()['detail']
        self.assertIn({'label': 'Future case field:', 'value': 'Preserved case value'},
                      detail['case_attributes'])
        self.assertIn({'label': 'Future description field:', 'value': 'Preserved description value'},
                      detail['description_attributes'])

    def test_profile_photos_are_absolute_and_exclude_site_logos(self):
        record = self.extract()
        expected = ['https://crimestoppers-uk.org/photo-501.jpg', 'https://example.test/second.jpg']
        self.assertEqual(record['detail']['photo_urls'], expected)
        self.assertEqual([a['url'] for a in record['attachments'] if a['type'] == 'Photograph'], expected)
        self.assertEqual(record['list']['thumbnail_url'], 'https://crimestoppers-uk.org/photo-501.jpg')

    def test_detail_html_is_saved_and_same_reference_does_not_merge_appeals(self):
        first = self.extract()
        second = self.extract('502', 'profile_unknown.html')
        self.assertEqual(first['detail']['cs_reference'], second['detail']['cs_reference'])
        self.assertNotEqual(first['source_record_id'], second['source_record_id'])
        for record in (first, second):
            path = Path(record['detail_file_path'])
            self.assertTrue(path.is_file())
            self.assertEqual(path.name, record['source_record_id'] + '.html')

    def test_challenge_page_fails_instead_of_reporting_complete_pagination(self):
        response = HtmlResponse(BASE + '?page=2', body=b'<html><body>Access challenge</body></html>',
                                encoding='utf-8')
        with self.assertRaises(PaginationError):
            list(self.spider.parse(response))
        self.assertFalse(self.spider.pagination_state['complete'])
        self.assertFalse(self.storage.source_file_path.exists())


if __name__ == '__main__':
    unittest.main()
