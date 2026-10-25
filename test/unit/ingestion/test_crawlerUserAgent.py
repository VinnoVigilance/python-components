import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from scrapy import Request
from scrapy.downloadermiddlewares.useragent import UserAgentMiddleware
from scrapy.settings import Settings

from ingestion.crawler.configLoader import load_crawler_config
from ingestion.crawler.crawler import crawl_source
from ingestion.crawler.models import CrawlerTask


AGENT = 'ExampleWatchlist/1.0 contact@example.org'
ENV_NAME = 'VV_TEST_HTTP_AGENT'
ROOT = Path(__file__).resolve().parents[3]


class TestCrawlerUserAgent(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def run_source(self, options=None, *, media=False, environment=None, root_options=None):
        config = {'fetch_strategy': 'direct'}
        if media:
            config['acquisition'] = {'spider': 'media', 'fetch_strategy': 'direct'}
            if options is not None:
                config['acquisition']['crawler_settings'] = options
            if root_options is not None:
                config['crawler_settings'] = root_options
        elif options is not None:
            config['crawler_settings'] = options
        settings = Settings()
        process = MagicMock()
        task = CrawlerTask(url='https://example.test/list', source_name='TEST',
                           list_name='TEST-LIST', source_config=config,
                           download_dir=self.temp.name)
        with (
            patch.dict(os.environ, environment or {}, clear=True),
            patch('ingestion.crawler.crawler.get_project_settings', return_value=settings),
            patch('ingestion.crawler.crawler.CrawlerProcess', return_value=process) as factory,
        ):
            self.process_factory = factory
            result = crawl_source(task)
        factory.assert_called_once_with(settings=settings)
        process.start.assert_called_once()
        return settings, result

    def test_watchlist_reads_agent_from_named_environment_variable(self):
        settings, _ = self.run_source({'user_agent_env': ENV_NAME},
                                      environment={ENV_NAME: AGENT})
        self.assertEqual(settings['USER_AGENT'], AGENT)

    def test_literal_agent_is_supported_for_other_sources(self):
        settings, _ = self.run_source({'user_agent': AGENT})
        self.assertEqual(settings['USER_AGENT'], AGENT)

    def test_undeclared_agent_keeps_previous_default(self):
        settings, _ = self.run_source()
        self.assertEqual(settings['USER_AGENT'],
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

    def test_environment_alone_does_not_override_another_source(self):
        settings, _ = self.run_source(environment={ENV_NAME: AGENT,
                                                   'SEC_HTTP_USER_AGENT': AGENT})
        self.assertTrue(settings['USER_AGENT'].startswith('Mozilla/5.0'))

    def test_missing_or_empty_required_variable_fails_before_requests_start(self):
        for environment in ({}, {ENV_NAME: ''}, {ENV_NAME: '   '}):
            with self.subTest(environment=environment):
                with self.assertRaisesRegex(ValueError, ENV_NAME):
                    self.run_source({'user_agent_env': ENV_NAME}, environment=environment)
                self.process_factory.assert_not_called()

    def test_conflicting_settings_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'only one'):
            self.run_source({'user_agent': AGENT, 'user_agent_env': ENV_NAME},
                            environment={ENV_NAME: AGENT})

    def test_invalid_literal_or_variable_name_is_rejected(self):
        for key in ('user_agent', 'user_agent_env'):
            for value in ('', '   ', None, 123):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.run_source({key: value})

    def test_multiline_header_is_rejected_without_echoing_its_value(self):
        bad = AGENT + '\r\nX-Unwanted: value'
        for config, environment in [({'user_agent': bad}, {}),
                                     ({'user_agent_env': ENV_NAME}, {ENV_NAME: bad})]:
            with self.subTest(config=config), self.assertRaises(ValueError) as error:
                self.run_source(config, environment=environment)
            self.assertNotIn(bad, str(error.exception))

    def test_surrounding_whitespace_is_removed(self):
        settings, _ = self.run_source({'user_agent_env': ' ' + ENV_NAME + ' '},
                                      environment={ENV_NAME: ' ' + AGENT + ' '})
        self.assertEqual(settings['USER_AGENT'], AGENT)

    def test_media_uses_its_acquisition_settings(self):
        settings, _ = self.run_source({'user_agent_env': ENV_NAME}, media=True,
                                      environment={ENV_NAME: AGENT})
        self.assertEqual(settings['USER_AGENT'], AGENT)

    def test_media_does_not_pick_up_watchlist_level_settings(self):
        settings, _ = self.run_source(media=True, root_options={'user_agent': AGENT})
        self.assertTrue(settings['USER_AGENT'].startswith('Mozilla/5.0'))

    def test_throttle_and_retry_settings_are_preserved(self):
        settings, _ = self.run_source({'user_agent': AGENT,
            'concurrent_requests_per_domain': 1, 'download_delay': 1,
            'randomize_download_delay': False, 'retry_times': 2,
            'retry_http_codes': [429, 500, 503]})
        self.assertEqual(settings.getint('CONCURRENT_REQUESTS_PER_DOMAIN'), 1)
        self.assertEqual(settings.getfloat('DOWNLOAD_DELAY'), 1.0)
        self.assertFalse(settings.getbool('RANDOMIZE_DOWNLOAD_DELAY'))
        self.assertEqual(settings.getint('RETRY_TIMES'), 2)
        self.assertEqual(settings.getlist('RETRY_HTTP_CODES'), [429, 500, 503])

    def test_scrapy_middleware_sets_agent_for_listing_and_detail_requests(self):
        settings, _ = self.run_source({'user_agent_env': ENV_NAME},
                                      environment={ENV_NAME: AGENT})
        middleware = UserAgentMiddleware(settings['USER_AGENT'])
        for url in ('https://example.test/list', 'https://example.test/profiles/one'):
            request = Request(url)
            middleware.process_request(request)
            self.assertEqual(request.headers['User-Agent'].decode(), AGENT)

    def test_sec_yaml_selects_direct_transport_and_named_agent(self):
        config = load_crawler_config(str(ROOT / 'config/watchlistSources/sec_us_pause.yaml'))
        self.assertEqual(config['fetch_strategy'], 'direct')
        self.assertEqual(config['crawler_settings']['user_agent_env'], 'SEC_HTTP_USER_AGENT')
        self.assertEqual(config['pagination']['type'], 'next_link')
        self.assertEqual(config['crawler_settings']['concurrent_requests_per_domain'], 1)
        self.assertEqual(config['crawler_settings']['download_delay'], 1.0)


if __name__ == '__main__':
    unittest.main()
