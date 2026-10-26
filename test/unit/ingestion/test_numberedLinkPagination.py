import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qsl, urlsplit

from scrapy.http import HtmlResponse

from ingestion.crawler.pagination import (
    PaginationError, resolve_next_page_url, resolve_numbered_page_url,
)
from ingestion.crawler.spiders.genericSpider import GenericSpider


BASE = 'https://example.test/list'


class TestNumberedPageUrl(unittest.TestCase):
    def test_adds_page_to_unfiltered_listing(self):
        self.assertEqual(resolve_numbered_page_url(BASE, '2', 'page'), BASE + '?page=2')

    def test_replaces_page_and_keeps_repeated_and_empty_filters(self):
        result = resolve_numbered_page_url(BASE + '?region=a&region=b&page=1&keyword=', '2', 'page')
        self.assertEqual(parse_qsl(urlsplit(result).query, keep_blank_values=True),
                         [('region', 'a'), ('region', 'b'), ('keyword', ''), ('page', '2')])

    def test_encoded_filter_and_fragment_are_handled(self):
        result = resolve_numbered_page_url(BASE + '?keyword=a%2Bb%20c#results', '2', 'page')
        self.assertEqual(parse_qsl(urlsplit(result).query), [('keyword', 'a+b c'), ('page', '2')])
        self.assertEqual(urlsplit(result).fragment, '')

    def test_zero_based_pagers_and_normalized_digits_are_supported(self):
        self.assertEqual(resolve_numbered_page_url(BASE + '?offset=1', '0', 'offset'), BASE + '?offset=0')
        self.assertEqual(resolve_numbered_page_url(BASE, ' 002 ', 'page'), BASE + '?page=2')

    def test_invalid_page_values_are_rejected(self):
        for value in (None, '', ' ', '#', '-1', '2.5', '2&filter=other',
                      'javascript:void(0)', 'https://other.test', '2/3', True, 2):
            with self.subTest(value=value), self.assertRaises(PaginationError):
                resolve_numbered_page_url(BASE, value, 'page')

    def test_invalid_parameter_names_are_rejected(self):
        for value in (None, '', ' ', 'page&filter', 'page=2', 1):
            with self.subTest(value=value), self.assertRaises(PaginationError):
                resolve_numbered_page_url(BASE, '2', value)

    def test_self_and_visited_pages_are_rejected(self):
        with self.assertRaises(PaginationError):
            resolve_numbered_page_url(BASE + '?page=2', '2', 'page')
        with self.assertRaises(PaginationError):
            resolve_numbered_page_url(BASE + '?page=2', '1', 'page',
                                     visited_urls=[BASE + '?page=1'])

    def test_unsupported_origin_scheme_is_rejected(self):
        with self.assertRaises(PaginationError):
            resolve_numbered_page_url('file:///list', '2', 'page')

    def test_original_href_resolver_still_works(self):
        self.assertEqual(resolve_next_page_url(BASE, '?page=2'), BASE + '?page=2')


class TestNumberedPagerSpider(unittest.TestCase):
    def setUp(self):
        self.state = {'complete': False, 'error': None}
        self.storage = Mock()
        self.options = {
            'type': 'next_link',
            'next_link_selector_type': 'xpath',
            'next_link_selector': '//li[@class="active"]/following-sibling::li[1]/a',
            'next_link_attribute': 'value',
            'next_link_query_param': 'page',
            'required_page_selector': 'form.pager',
        }
        self.config = {
            'discovery': {'row_selector': '.row', 'detail_link_selector': 'a'},
            'record_id': {'strategy': 'url_regex', 'pattern': r'/profiles/(\d+)'},
            'pagination': self.options,
            'storage': {'save_listing_page': True},
        }
        self.spider = GenericSpider(task=SimpleNamespace(url=BASE, detail_items=None),
            crawler_config=self.config, storage=self.storage, records=[],
            pagination_state=self.state)

    def page(self, number=1, next_value='2', *, marker=True, extra=''):
        following = '' if next_value is None else (
            '<li><a href="#" value="' + next_value + '">Next</a></li>')
        content = ('<form class="pager"><ul><li class="active"><a>' + str(number)
            + '</a></li>' + following + extra + '</ul></form>') if marker else '<h1>Challenge</h1>'
        return HtmlResponse(BASE + '?page=' + str(number), encoding='utf-8',
            body=('<html><body><div class="row"><a href="/profiles/' + str(number)
                  + '">Name</a></div>' + content + '</body></html>').encode())

    def test_numeric_attribute_builds_get_request_without_javascript(self):
        requests = list(self.spider.parse(self.page()))
        self.assertEqual(len(requests), 2)
        following = requests[-1]
        self.assertEqual(following.url, BASE + '?page=2')
        self.assertEqual(following.method, 'GET')
        self.assertEqual(following.errback, self.spider.listing_failed)
        self.assertFalse(self.state['complete'])

    def test_last_page_completes_and_saves_all_listing_sections(self):
        list(self.spider.parse(self.page()))
        requests = list(self.spider.parse(self.page(2, None)))
        self.assertEqual(len(requests), 1)
        self.assertTrue(self.state['complete'])
        content = self.storage.save_source_html.call_args.args[0]
        self.assertIn(BASE + '?page=1', content)
        self.assertIn(BASE + '?page=2', content)

    def test_missing_page_marker_fails_instead_of_signalling_source_end(self):
        with self.assertRaisesRegex(PaginationError, 'marker'):
            list(self.spider.parse(self.page(marker=False)))
        self.assertFalse(self.state['complete'])
        self.assertIn('marker', self.state['error'])
        self.storage.save_source_html.assert_not_called()

    def test_missing_numeric_attribute_is_an_error(self):
        page = HtmlResponse(BASE, encoding='utf-8',
            body=b'<form class="pager"><li class="active"></li><li><a href="#">2</a></li></form>')
        with self.assertRaisesRegex(PaginationError, 'value'):
            self.spider._next_listing_url(page)

    def test_invalid_value_sets_incomplete_state(self):
        with self.assertRaises(PaginationError):
            list(self.spider.parse(self.page(next_value='bad')))
        self.assertFalse(self.state['complete'])
        self.assertIsNotNone(self.state['error'])

    def test_page_limit_does_not_report_success(self):
        self.options['max_pages'] = 1
        with self.assertRaisesRegex(PaginationError, 'max_pages'):
            list(self.spider.parse(self.page()))
        self.assertFalse(self.state['complete'])

    def test_server_ignoring_page_parameter_is_detected_as_loop(self):
        list(self.spider.parse(self.page()))
        with self.assertRaisesRegex(PaginationError, 'loop'):
            list(self.spider.parse(self.page(2, '2')))

    def test_custom_url_attribute_without_numeric_mode_is_supported(self):
        self.options.pop('next_link_query_param')
        self.options['next_link_attribute'] = 'data-url'
        page = HtmlResponse(BASE, encoding='utf-8', body=b'<form class="pager">'
            b'<li class="active"></li><li><a data-url="?page=2">2</a></li></form>')
        self.assertEqual(self.spider._next_listing_url(page), BASE + '?page=2')

    def test_conflicting_numeric_next_pages_are_rejected(self):
        self.options['next_link_selector'] = '//li[not(@class)]/a'
        with self.assertRaisesRegex(PaginationError, 'Multiple'):
            self.spider._next_listing_url(self.page(extra='<li><a value="3">3</a></li>'))

    def test_default_href_mode_remains_unchanged(self):
        self.config['pagination'] = {'type': 'next_link', 'next_link_selector': 'a[rel="next"]'}
        page = HtmlResponse(BASE, encoding='utf-8', body=b'<a rel="next" href="?page=2">Next</a>')
        self.assertEqual(self.spider._next_listing_url(page), BASE + '?page=2')


if __name__ == '__main__':
    unittest.main()
