"""Source registration, primary image selection and evidence paths; no IO services."""

import json
import importlib.util
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from scripts.watchlist.sourceFiles import find_source_file
from services.watchlistPipeline.attachmentPaths import list_attachment_paths
from utils.hashing import calculate_file_hash


ROOT = Path(__file__).resolve().parents[4]
SOURCE = 'PDEA_MOST_WANTED'


class TestPdeaSourceIntegration(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.downloads = self.root / 'downloads'
        self.day = self.downloads / 'PDEA' / SOURCE / 'year=2026/month=10/day=27'
        self.day.mkdir(parents=True)
        self.image = self.day / 'PDEA_capture_a.jpg'
        self.image.write_bytes(b'original poster bytes')
        self.html = self.image.with_suffix('.source.html')
        self.manifest = self.image.with_suffix('.manifest.json')
        self.html.write_text('<html>source evidence</html>', encoding='utf-8')
        self.manifest.write_text('{"schema_version": 1}', encoding='utf-8')
        self.config = deepcopy(WATCHLIST_CONFIGS[SOURCE])

    def select(self, config=None):
        return find_source_file(self.downloads, self.root, SOURCE, config or self.config)

    def test_main_registry_contains_inline_config(self):
        from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
        self.assertIn(SOURCE, WATCHLIST_CONFIGS, 'Add the PDEA dictionary entry inside WATCHLIST_CONFIGS.')
        self.assertEqual(WATCHLIST_CONFIGS[SOURCE], self.config)

    def test_job_identity_schedule_and_version_settings(self):
        for key, value in {
            'external_id_path': 'unique_id', 'schedule': 'weekly',
            'versioning_strategy': 'continuous', 'minimum_record_count': 16,
            'file_type': 'image', 'download_method': 'BYPASS',
        }.items():
            self.assertEqual(self.config[key], value)

    def test_production_config_runs_fresh_ocr_in_configured_runtime(self):
        self.assertEqual(self.config['parser']['runtime']['mode'], 'subprocess')
        self.assertEqual(self.config['parser']['runtime']['python_env'], 'VV_OCR_PYTHON')
        self.assertNotIn('saved_ocr_path', self.config['parser'])

    def test_acquisition_saves_image_as_the_primary_file(self):
        actions = self.config['bypass_config']['actions']
        self.assertEqual([a['action'] for a in actions if a['action'].startswith('save_')], ['save_image'])
        self.assertEqual(next(a for a in actions if a['action'] == 'save_image')['image']['prefer'], 'largest_srcset')

    def test_newer_html_and_manifest_cannot_be_selected_as_image(self):
        os.utime(self.image, (10, 10))
        os.utime(self.html, (20, 20))
        os.utime(self.manifest, (30, 30))
        self.assertEqual(self.select(), self.image)

    def test_latest_image_is_selected_across_supported_extensions(self):
        other = self.day / 'capture_b.PNG'
        other.write_bytes(b'new poster')
        os.utime(self.image, (10, 10))
        os.utime(other, (20, 20))
        self.assertEqual(self.select(), other)

    def test_image_attachments_are_excluded(self):
        attachment = self.day / 'attachments/profile.jpg'
        attachment.parent.mkdir()
        attachment.write_bytes(b'attachment')
        os.utime(self.image, (10, 10))
        os.utime(attachment, (20, 20))
        self.assertEqual(self.select(), self.image)

    def test_source_folder_named_images_is_not_silently_skipped(self):
        directory = self.downloads / 'IMAGE_SOURCE' / SOURCE
        directory.mkdir(parents=True)
        image = directory / 'source.jpeg'
        image.write_bytes(b'poster')
        self.assertEqual(self.select({**self.config, 'source_name': 'IMAGE_SOURCE'}), image)

    def test_local_image_override_is_supported_but_html_override_fails(self):
        self.assertEqual(self.select({**self.config, 'local_path': str(self.image)}), self.image)
        with self.assertRaisesRegex(ValueError, 'not an image'):
            self.select({**self.config, 'local_path': str(self.html)})

    def test_legacy_xml_file_selection_is_preserved(self):
        xml = self.day / 'source.xml'
        xml.write_text('<root/>', encoding='utf-8')
        self.assertEqual(self.select({**self.config, 'file_type': 'xml'}), xml)

    def test_only_current_capture_evidence_is_selected(self):
        (self.day / 'other_capture.source.html').write_text('old', encoding='utf-8')
        paths = list_attachment_paths(self.image, self.config['attachments'][0])
        self.assertEqual(paths, [self.html, self.manifest])
        self.assertNotIn(self.image, paths)

    def test_missing_required_evidence_does_not_succeed_silently(self):
        self.manifest.unlink()
        with self.assertRaisesRegex(FileNotFoundError, 'Required source evidence'):
            list_attachment_paths(self.image, self.config['attachments'][0])

    def test_optional_missing_evidence_can_be_skipped_explicitly(self):
        self.manifest.unlink()
        rule = {**self.config['attachments'][0], 'required': False}
        self.assertEqual(list_attachment_paths(self.image, rule), [self.html])

    def test_attachment_templates_cannot_escape_the_source_directory(self):
        for template in ('../outside.html', str(self.root / 'outside.html'), '{source_stem.__class__}'):
            with self.subTest(template=template), self.assertRaises(ValueError):
                list_attachment_paths(self.image, {'local_files': [template]})

    def test_duplicate_attachment_paths_are_only_returned_once(self):
        self.assertEqual(list_attachment_paths(self.image, {'local_files': ['{source_stem}.source.html'] * 2}), [self.html])

    def test_legacy_list_directory_behavior_is_preserved(self):
        directory = self.day / 'evidence'
        directory.mkdir()
        child = directory / 'original.pdf'
        child.write_bytes(b'pdf')
        self.assertEqual(list_attachment_paths(self.image, {'local_directory': 'evidence'}), [child])
        self.assertEqual(list_attachment_paths(self.image, {'local_directory': 'missing'}), [])

    def test_primary_hash_ignores_filename_and_evidence_changes(self):
        before = calculate_file_hash(self.image)
        self.html.write_text('new rendered HTML', encoding='utf-8')
        self.manifest.write_text('{"new_timestamp": true}', encoding='utf-8')
        renamed = self.day / 'different_capture.jpg'
        renamed.write_bytes(self.image.read_bytes())
        self.assertEqual(calculate_file_hash(self.image), before)
        self.assertEqual(calculate_file_hash(renamed), before)
        renamed.write_bytes(b'new image bytes')
        self.assertNotEqual(calculate_file_hash(renamed), before)

    def pipeline_with_fake_io(self, duplicate_status):
        """Load the actual orchestrator with its I/O service boundary mocked.

        This needs no DB/browser/storage packages and never reads config secrets.
        The orchestration function itself is not copied or replaced.
        """
        acquisition = SimpleNamespace(source_file_path=self.image, records=None, broken_details=[])
        file_service = SimpleNamespace(
            AcquisitionResult=SimpleNamespace,
            acquire_source=MagicMock(return_value=acquisition),
            calculate_file_metadata=MagicMock(side_effect=lambda file_path: {'file_hash': calculate_file_hash(file_path)}),
            resolve_lookup_values=MagicMock(return_value={'source_id': 1, 'list_type_id': 2}),
            check_duplicate=MagicMock(return_value={
                'duplicate_status': duplicate_status, 'watchlist_file_id': 10,
                'file_version': '1', 'storage_path': 'stored/original.jpg',
            }),
            insert_file_log=MagicMock(), determine_file_version=MagicMock(return_value='2'),
            store_source_file=MagicMock(return_value='stored/new.jpg'),
            insert_watchlist_file=MagicMock(return_value=11),
        )
        raw_service = SimpleNamespace(process_watchlist_source=MagicMock(return_value={
            'parsed_record_count': 16, 'processed_record_count': 16,
            'raw_record_count': 16, 'broken_detail_urls': [],
        }))
        core_service = SimpleNamespace(process_watchlist_file=MagicMock(return_value={
            'processed_count': 16, 'new_count': 16, 'updated_count': 0,
            'skipped_count': 0, 'deleted_count': 0,
        }))
        attachment_service = SimpleNamespace(process_attachments=MagicMock(return_value={
            'processed_count': 2, 'new_count': 2, 'reused_count': 0,
            'member_mapping_count': 0, 'list_mapping_count': 2,
        }))
        services = ModuleType('services.watchlistPipeline')
        services.watchlistFileService = file_service
        services.watchlistRawService = raw_service
        services.watchlistCoreService = core_service
        services.watchlistAttachmentService = attachment_service
        downloader = ModuleType('ingestion.downloader')
        downloader.interface = SimpleNamespace()
        logging_config = ModuleType('config.loggingConfig')
        logging_config.configure_logging = lambda: None
        replacements = {
            'services.watchlistPipeline': services,
            'ingestion.downloader': downloader,
            'config.loggingConfig': logging_config,
        }
        spec = importlib.util.spec_from_file_location('_pdea_pipeline_flow_test', ROOT / 'pipelines/watchlistPipeline.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, replacements):
            spec.loader.exec_module(module)
        result = module.run_watchlist_pipeline(SOURCE)
        return result, file_service, raw_service, core_service, attachment_service

    def test_completed_image_duplicate_skips_parsing_ocr_storage_and_core(self):
        result, files, raw, core, attachments = self.pipeline_with_fake_io('DUPLICATE_COMPLETED')
        self.assertEqual(result['pipeline_result'], 'SKIPPED')
        files.check_duplicate.assert_called_once_with(source_id=1, list_type_id=2, file_hash=calculate_file_hash(self.image))
        files.store_source_file.assert_not_called()
        raw.process_watchlist_source.assert_not_called()
        core.process_watchlist_file.assert_not_called()
        attachments.process_attachments.assert_not_called()

    def test_new_image_version_continues_with_the_image_and_its_evidence(self):
        result, files, raw, core, attachments = self.pipeline_with_fake_io('NEW_VERSION')
        self.assertEqual(result['pipeline_result'], 'NORMALIZED')
        self.assertEqual(result['core_new_count'], 16)
        self.assertEqual(files.store_source_file.call_args.kwargs['file_path'], self.image)
        self.assertEqual(raw.process_watchlist_source.call_args.kwargs['acquisition'].source_file_path, self.image)
        self.assertEqual(attachments.process_attachments.call_args.kwargs['source_file_path'], self.image)
        core.process_watchlist_file.assert_called_once()


if __name__ == '__main__':
    unittest.main()
