"""Real collector/action orchestration with a mocked browser; no live requests."""

import json
import tempfile
import unittest
from pathlib import Path

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from copy import deepcopy
from unittest.mock import MagicMock, patch

from ingestion.bypassCollector import BypassCollector


ROOT = Path(__file__).resolve().parents[3]


class TestSourceImageAcquisition(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.config = deepcopy(WATCHLIST_CONFIGS["PDEA_MOST_WANTED"])
        fixture = ROOT / 'test/fixtures/watchlist/pdea_most_wanted'
        self.html = (fixture / 'source_page.html').read_text(encoding='utf-8')
        self.image = (fixture / 'source_image.jpg').read_bytes()
        self.engine = MagicMock()
        self.engine.__enter__.return_value = self.engine
        self.engine.navigate.return_value = True
        self.engine.waitForElement.return_value = True
        self.engine.getHtml.return_value = self.html
        self.engine.getCurrentUrl.return_value = self.config['url']
        self.collector = BypassCollector(outputDir=self.folder)

    def test_configured_collector_returns_image_and_preserves_page_sidecars(self):
        response = {'final_url': 'https://pdea.gov.ph/wp-content/uploads/2023/06/mostwanted.jpg',
                    'content_type': 'image/jpeg'}
        with patch.object(self.collector, '_createEngine', return_value=self.engine):
            with patch.object(self.collector, '_cleanupBrowserArtifacts') as cleanup:
                with patch('ingestion.bypassCollector.actions.saveImageAction.fetch_browser_binary',
                           return_value=(self.image, response)):
                    path = self.collector.collect(self.config)
        self.assertIsNotNone(path)
        self.assertEqual(path.suffix, '.jpg')
        self.assertEqual(path.read_bytes(), self.image)
        self.assertTrue(path.with_suffix('.source.html').is_file())
        self.assertTrue(path.with_suffix('.manifest.json').is_file())
        self.engine.waitForElement.assert_called_once()
        self.engine.__exit__.assert_called_once()
        cleanup.assert_called_once()

    def test_failed_image_request_cannot_return_html_as_success(self):
        with patch.object(self.collector, '_createEngine', return_value=self.engine):
            with patch.object(self.collector, '_cleanupBrowserArtifacts'):
                with patch('ingestion.bypassCollector.actions.saveImageAction.fetch_browser_binary',
                           side_effect=RuntimeError('HTTP 403')):
                    with self.assertLogs('ingestion.bypassCollector', level='ERROR'):
                        self.assertIsNone(self.collector.collect(self.config))

    def test_pdea_uses_existing_safe_get_navigation_without_reconnect_options(self):
        with patch('ingestion.bypassCollector.collector.StealthBrowserEngine') as engine:
            self.collector._createEngine(self.config['bypass_config'])
        self.assertEqual(engine.call_args.kwargs['navigationMode'], 'safe_get')
        self.assertNotIn('reconnectSeconds', engine.call_args.kwargs)

    def test_legacy_navigation_default_remains_safe_get(self):
        with patch('ingestion.bypassCollector.collector.StealthBrowserEngine') as engine:
            self.collector._createEngine({'challenge': 'cloudflare'})
        self.assertEqual(engine.call_args.kwargs['navigationMode'], 'safe_get')


if __name__ == '__main__':
    unittest.main()
