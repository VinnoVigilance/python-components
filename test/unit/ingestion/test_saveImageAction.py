"""Image capture contracts with real image bytes and a mocked browser transport."""

import base64
import hashlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from PIL import Image

from ingestion.bypassCollector.actions.saveImageAction import SaveImageAction
from ingestion.bypassCollector.browserBinaryDownload import fetch_browser_binary


ROOT = Path(__file__).resolve().parents[3]


class TestBrowserImageDownload(unittest.TestCase):
    def setUp(self):
        self.url = 'https://example.org/poster.jpg'
        self.result = {'ok': True, 'status': 200, 'contentType': 'image/jpeg',
                       'finalUrl': self.url, 'size': 3, 'base64': base64.b64encode(b'abc').decode()}
        self.driver = SimpleNamespace(timeouts=SimpleNamespace(script=30),
                                      set_script_timeout=Mock(), execute_async_script=Mock())
        self.engine = SimpleNamespace(navigationMode='uc_open',
                                      sb=SimpleNamespace(driver=self.driver), evaluateAwait=Mock())
        self.driver.execute_async_script.return_value = json.dumps(self.result)

    def fetch(self, **kwargs):
        return fetch_browser_binary(self.engine, self.url,
                                    page_url='https://example.org/list', **kwargs)

    def test_uc_download_restores_script_timeout_and_returns_exact_bytes(self):
        payload, response = self.fetch(timeout_seconds=20)
        self.assertEqual(payload, b'abc')
        self.assertEqual(response['final_url'], self.url)
        self.assertEqual(self.driver.set_script_timeout.call_args_list, [call(30), call(30)])
        self.engine.evaluateAwait.assert_not_called()

    def test_driver_failure_still_restores_timeout(self):
        self.driver.execute_async_script.side_effect = RuntimeError('browser disconnected')
        with self.assertRaises(RuntimeError):
            self.fetch()
        self.assertEqual(self.driver.set_script_timeout.call_args_list, [call(55), call(30)])

    def test_non_uc_engine_uses_its_promise_evaluator(self):
        self.engine.navigationMode = 'safe_get'
        self.engine.sb.cdp = object()
        self.engine.evaluateAwait.return_value = self.result
        self.assertEqual(self.fetch()[0], b'abc')
        self.driver.execute_async_script.assert_not_called()

    def test_non_cdp_session_uses_native_async_even_without_uc_mode_name(self):
        self.engine.navigationMode = 'safe_get'
        self.assertEqual(self.fetch()[0], b'abc')
        self.engine.evaluateAwait.assert_not_called()

    def test_other_origins_are_rejected_before_browser_execution(self):
        self.url = 'https://other.example/poster.jpg'
        with self.assertRaisesRegex(ValueError, 'same origin'):
            self.fetch()
        self.driver.execute_async_script.assert_not_called()

    def test_invalid_http_base64_size_and_redirect_responses_are_rejected(self):
        for change in ({'ok': False, 'error': 'HTTP 403'}, {'status': 403},
                       {'size': 99}, {'base64': '??invalid??'},
                       {'finalUrl': 'https://other.example/image.jpg'}, {'size': 0}):
            self.driver.execute_async_script.return_value = {**self.result, **change}
            with self.subTest(change=change):
                with self.assertRaises((ValueError, RuntimeError)):
                    self.fetch()

    def test_configured_size_limit_applies_to_returned_bytes(self):
        with self.assertRaisesRegex(ValueError, 'byte count'):
            self.fetch(max_bytes=2)

    def test_invalid_limits_do_not_execute_javascript(self):
        for kwargs in ({'timeout_seconds': 0}, {'max_bytes': True}, {'timeout_seconds': 1.5}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.fetch(**kwargs)
        self.driver.execute_async_script.assert_not_called()

    def test_cache_mode_is_explicit_and_legacy_default_is_no_store(self):
        self.fetch()
        self.assertIn('"cacheMode": "no-store"', self.driver.execute_async_script.call_args.args[0])
        self.fetch(cache_mode="force-cache")
        self.assertIn('"cacheMode": "force-cache"', self.driver.execute_async_script.call_args.args[0])

    def test_invalid_cache_mode_is_rejected_before_browser_execution(self):
        with self.assertRaisesRegex(ValueError, "cache_mode"):
            self.fetch(cache_mode="unknown")
        self.driver.execute_async_script.assert_not_called()


class TestSaveImageAction(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.config = deepcopy(WATCHLIST_CONFIGS["PDEA_MOST_WANTED"])
        self.action_config = deepcopy(self.config['bypass_config']['actions'][-1])
        self.html = (ROOT / 'test/fixtures/watchlist/pdea_most_wanted/source_page.html').read_text(encoding='utf-8')
        self.payload = (ROOT / 'test/fixtures/watchlist/pdea_most_wanted/source_image.jpg').read_bytes()
        self.context = {'outputDir': self.folder, 'source_name': 'PDEA', 'list_name': 'PDEA_MOST_WANTED',
                        'url': self.config['url']}
        self.engine = SimpleNamespace(getHtml=Mock(return_value=self.html),
                                      getCurrentUrl=Mock(return_value=self.config['url']))
        self.fetcher = patch('ingestion.bypassCollector.actions.saveImageAction.fetch_browser_binary').start()
        self.addCleanup(patch.stopall)
        self.fetcher.return_value = (self.payload, {'final_url': 'https://pdea.gov.ph/wp-content/uploads/2023/06/mostwanted.jpg',
                                                  'content_type': 'image/jpeg'})

    def execute(self):
        return SaveImageAction().execute(self.action_config, self.context, self.engine)

    def test_capture_preserves_exact_bytes_html_hashes_and_original_dimensions(self):
        self.assertTrue(self.execute())
        path = Path(self.context['savedFile'])
        self.assertEqual(path.read_bytes(), self.payload)
        self.assertEqual(path.suffix, '.jpg')
        self.assertEqual(path.with_suffix('.source.html').read_text(encoding='utf-8'), self.html)
        manifest = json.loads(path.with_suffix('.manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['image']['sha256'], hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(manifest['image']['width'], 1277)
        self.assertEqual(manifest['image']['height'], 1026)
        self.assertEqual(manifest['image']['size_bytes'], len(self.payload))
        self.assertEqual(manifest['html']['sha256'], hashlib.sha256(self.html.encode()).hexdigest())
        self.assertEqual(manifest['selection']['descriptor'], '1277w')
        self.assertEqual(self.fetcher.call_args.args[1], manifest['image_url'])
        self.assertEqual(set(p.name for p in path.parent.iterdir()),
                         {path.name, path.with_suffix('.source.html').name, path.with_suffix('.manifest.json').name})

    def test_repeated_captures_keep_day_directory_and_unique_storage_names(self):
        self.assertTrue(self.execute())
        first = Path(self.context['savedFile'])
        self.assertTrue(self.execute())
        second = Path(self.context['savedFile'])
        self.assertEqual(first.parent, second.parent)
        self.assertNotEqual(first.name, second.name)
        self.assertTrue(first.is_file())

    def test_html_error_body_cannot_be_saved_as_an_image(self):
        self.fetcher.return_value = (b'<html>Just a moment...</html>', {'final_url': 'https://pdea.gov.ph/x',
                                                                       'content_type': 'text/html'})
        with self.assertLogs('ingestion.bypassCollector.actions.saveImageAction', level='ERROR'):
            self.assertFalse(self.execute())
        self.assertNotIn('savedFile', self.context)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_smaller_image_is_rejected_even_if_srcset_claims_full_size(self):
        buffer = io.BytesIO()
        with Image.new('RGB', (300, 241), 'white') as image:
            image.save(buffer, 'JPEG')
        self.fetcher.return_value = (buffer.getvalue(), {'final_url': 'https://pdea.gov.ph/x',
                                                        'content_type': 'image/jpeg'})
        with self.assertLogs('ingestion.bypassCollector.actions.saveImageAction', level='ERROR'):
            self.assertFalse(self.execute())
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_srcset_dimension_mismatch_is_rejected_after_minimum_size_checks(self):
        self.action_config['min_width'] = self.action_config['min_height'] = 1
        buffer = io.BytesIO()
        with Image.new('RGB', (1200, 1000), 'white') as image:
            image.save(buffer, 'JPEG')
        self.fetcher.return_value = (buffer.getvalue(), {'final_url': 'https://pdea.gov.ph/x',
                                                        'content_type': 'image/jpeg'})
        with self.assertLogs('ingestion.bypassCollector.actions.saveImageAction', level='ERROR'):
            self.assertFalse(self.execute())
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_oversized_or_disallowed_image_is_not_published(self):
        for overrides in ({'max_pixels': 10}, {'allowed_formats': ['PNG']}):
            self.action_config.update(overrides)
            with self.subTest(overrides=overrides):
                with self.assertLogs('ingestion.bypassCollector.actions.saveImageAction', level='ERROR'):
                    self.assertFalse(self.execute())
            self.assertNotIn('savedFile', self.context)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_failed_transport_clears_stale_primary_and_does_not_publish(self):
        self.context['savedFile'] = 'old.html'
        self.fetcher.side_effect = RuntimeError('HTTP 403')
        with self.assertLogs('ingestion.bypassCollector.actions.saveImageAction', level='ERROR'):
            self.assertFalse(self.execute())
        self.assertNotIn('savedFile', self.context)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_partial_write_failure_cleans_only_the_new_capture(self):
        previous = self.folder / 'existing.txt'
        previous.write_text('keep')
        with patch('ingestion.bypassCollector.actions.saveImageAction.Path.write_text', side_effect=OSError('disk failed')):
            with self.assertLogs('ingestion.bypassCollector.actions.saveImageAction', level='ERROR'):
                self.assertFalse(self.execute())
        self.assertEqual(list(self.folder.iterdir()), [previous])
        self.assertNotIn('savedFile', self.context)

    def test_keyboard_interrupt_during_publication_removes_partial_capture(self):
        with patch('ingestion.bypassCollector.actions.saveImageAction.Path.write_text', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.execute()
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertNotIn('savedFile', self.context)

    def test_failed_commit_removes_already_published_sidecars(self):
        original = Path.replace
        committed = []

        def replace(source, target):
            if committed:
                raise OSError('commit failed')
            result = original(source, target)
            committed.append(target)
            return result

        with patch('ingestion.bypassCollector.actions.saveImageAction.Path.replace', new=replace):
            with self.assertLogs('ingestion.bypassCollector.actions.saveImageAction', level='ERROR'):
                self.assertFalse(self.execute())
        self.assertEqual(len(committed), 1)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertNotIn('savedFile', self.context)


if __name__ == '__main__':
    unittest.main()
