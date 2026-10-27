"""Real subprocess transport with a fake recognizer; no models or network."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from parsing.ocr.documentOcrReader import DocumentOcrSettings
from parsing.ocr.documentOcrWorker import execute_request
from parsing.ocr.rapidOcrEngine import OcrSettings
from parsing.ocr.subprocessOcr import read_document_in_subprocess, validate_runtime
from parsing.ocrParser import OcrParser


FAKE_WORKER = '''
import argparse, hashlib, json, os, time
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('--request'); p.add_argument('--output')
a=p.parse_args(); q=json.loads(Path(a.request).read_text(encoding='utf-8'))
mode=os.environ.get('VV_TEST_OCR_WORKER_MODE','ok')
if mode=='timeout': time.sleep(5)
if mode=='error': print('SIMULATED_OCR_FAILURE',flush=True); raise SystemExit(7)
if mode=='missing': raise SystemExit(0)
out=Path(a.output)
if mode=='invalid_json': out.write_text('not JSON',encoding='utf-8'); raise SystemExit(0)
data=Path(q['source_path']).read_bytes()
d={'schema_version':1,'source_path':q['source_path'],
'source_sha256':hashlib.sha256(data).hexdigest(),'source_size_bytes':len(data),
'document_type':'image','document_page_count':1,'selected_pages':[1],
'processed_page_count':1,'all_pages_processed':True,
'pages':[{'schema_version':1,'page_number':1,'image_size':[20,10],
'coordinate_space':'upright_image_pixels','region':[0,0,20,10],
'status':'TEXT_FOUND','line_count':1,'settings':q['engine'],
'lines':[{'line_number':1,'text':'EXAMPLE NAME','score':0.98,
'box':[[1,1],[19,1],[19,9],[1,9]]}]}]}
if mode=='wrong_hash': d['source_sha256']='f'*64
if mode=='wrong_size': d['source_size_bytes']+=1
if mode=='wrong_pages': d['selected_pages']=[2]
if mode=='wrong_schema': d['schema_version']=99
if mode=='oversized': d['extra']='x'*10000
print('Recognizer logging is not the JSON protocol',flush=True)
out.write_text(json.dumps(d),encoding='utf-8')
'''


class TestSubprocessOcr(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='ocr runtime ')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source = self.root / 'source with spaces.png'
        with Image.new('RGB', (20, 10), 'white') as image:
            image.save(self.source)
        (self.root / 'fake_document_worker.py').write_text(FAKE_WORKER, encoding='utf-8')
        self.runtime = {'mode': 'subprocess', 'python_env': 'VV_TEST_OCR_PYTHON', 'timeout_seconds': 15}
        self.env = patch.dict(os.environ, {
            'VV_TEST_OCR_PYTHON': sys.executable,
            'VV_TEST_OCR_WORKER_MODE': 'ok',
            'PYTHONPATH': str(self.root) + os.pathsep + os.environ.get('PYTHONPATH', ''),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        module = patch('parsing.ocr.subprocessOcr.WORKER_MODULE', 'fake_document_worker')
        module.start()
        self.addCleanup(module.stop)

    def read(self, mode='ok', pages=None, runtime=None):
        with patch.dict(os.environ, {'VV_TEST_OCR_WORKER_MODE': mode}):
            return read_document_in_subprocess(
                self.source, OcrSettings(), DocumentOcrSettings(), pages,
                runtime or self.runtime,
            )

    def test_real_child_process_returns_document_and_keeps_logs_out_of_json(self):
        result = self.read(pages=[1])
        self.assertEqual(result['source_sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(result['pages'][0]['lines'][0]['text'], 'EXAMPLE NAME')
        self.assertEqual(result['source_path'], str(self.source))

    def test_image_parser_uses_the_runtime_without_loading_local_recognizer(self):
        layout = {'schema_version': 1, 'layout_id': 'EXAMPLE', 'layout_version': 1,
                  'fields': {'name': {'box': [0, 0, 1, 1], 'required': True}},
                  'pages': [{'page_number': 1, 'reference_size': [20, 10],
                             'record_regions': [{'key': 'one', 'box': [0, 0, 20, 10]}]}]}
        config = {'file_type': 'image', 'parser': {'layout': layout, 'runtime': self.runtime}}
        with patch('parsing.ocr.rapidOcrEngine.RapidOcrEngine._build_backend', side_effect=AssertionError('No local OCR')):
            result = OcrParser().parse(self.source, config)
        self.assertEqual(result[0]['name'], 'EXAMPLE NAME')
        self.assertEqual(result[0]['_ocr']['recognition_mode'], 'fresh')
        self.assertEqual(result[0]['_ocr']['execution_mode'], 'subprocess')

    def test_explicit_replay_does_not_require_or_launch_runtime(self):
        document = self.read()
        saved = self.root / 'replay.json'
        saved.write_text(json.dumps(document), encoding='utf-8')
        layout = {'schema_version': 1, 'layout_id': 'EXAMPLE', 'layout_version': 1,
                  'fields': {'name': {'box': [0, 0, 1, 1], 'required': True}},
                  'pages': [{'page_number': 1, 'reference_size': [20, 10],
                             'record_regions': [{'key': 'one', 'box': [0, 0, 20, 10]}]}]}
        os.environ.pop('VV_TEST_OCR_PYTHON', None)
        with patch('parsing.ocr.subprocessOcr.subprocess.run', side_effect=AssertionError('No launch in replay')):
            result = OcrParser().parse(self.source, {'file_type': 'image', 'parser': {
                'layout': layout, 'saved_ocr_path': str(saved), 'runtime': self.runtime,
            }})
        self.assertEqual(result[0]['_ocr']['recognition_mode'], 'replay')

    def test_unset_python_path_fails_explicitly_without_fallback(self):
        os.environ.pop('VV_TEST_OCR_PYTHON', None)
        with self.assertRaisesRegex(RuntimeError, 'Set VV_TEST_OCR_PYTHON'):
            self.read()

    def test_invalid_executable_path_is_rejected(self):
        for value in ('python', str(self.root / 'missing-python.exe')):
            with self.subTest(value=value), patch.dict(os.environ, {'VV_TEST_OCR_PYTHON': value}):
                with self.assertRaisesRegex(RuntimeError, 'existing absolute'):
                    self.read()

    def test_worker_failure_reports_exit_and_bounded_log_tail(self):
        with self.assertRaisesRegex(RuntimeError, r'(?s)exit 7.*SIMULATED_OCR_FAILURE'):
            self.read('error')

    def test_timeout_stops_worker_and_raises(self):
        with self.assertRaisesRegex(RuntimeError, 'exceeded'):
            self.read('timeout', runtime={**self.runtime, 'timeout_seconds': 0.1})

    def test_success_exit_without_document_is_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'no document'):
            self.read('missing')

    def test_malformed_json_is_not_accepted(self):
        with self.assertRaises(json.JSONDecodeError):
            self.read('invalid_json')

    def test_results_from_other_source_bytes_are_rejected(self):
        for mode in ('wrong_hash', 'wrong_size'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'source hash/size'):
                self.read(mode)

    def test_selected_pages_are_bound_to_request(self):
        with self.assertRaisesRegex(ValueError, 'selected pages'):
            self.read('wrong_pages', pages=[1])

    def test_invalid_document_schema_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'document schema'):
            self.read('wrong_schema')

    def test_output_size_is_limited(self):
        with self.assertRaisesRegex(ValueError, 'max_output_bytes'):
            self.read('oversized', runtime={**self.runtime, 'max_output_bytes': 1000})

    def test_source_size_limit_applies_before_launch(self):
        with patch('parsing.ocr.subprocessOcr.subprocess.run', side_effect=AssertionError('Must not launch')):
            with self.assertRaisesRegex(ValueError, 'max_source_bytes'):
                read_document_in_subprocess(self.source, OcrSettings(), DocumentOcrSettings(max_source_bytes=1), None, self.runtime)

    def test_runtime_validation_and_default_legacy_mode(self):
        self.assertEqual(validate_runtime({})['mode'], 'in_process')
        for invalid in ([], {'mode': 'automatic'}, {'unknown': True},
                        {'timeout_seconds': 0}, {'timeout_seconds': True},
                        {'timeout_seconds': float('nan')}, {'max_output_bytes': False},
                        {'python_env': 'A=B'}):
            with self.subTest(config=invalid), self.assertRaises(ValueError):
                validate_runtime(invalid)

    def test_worker_uses_shared_reader_and_explicit_settings(self):
        request = {'schema_version': 1, 'source_path': str(self.source),
                   'engine': asdict(OcrSettings()), 'document': asdict(DocumentOcrSettings()),
                   'page_numbers': [1]}
        before = deepcopy(request)
        with patch('parsing.ocr.documentOcrReader.DocumentOcrReader.read', return_value={'result': 'complete'}) as read:
            self.assertEqual(execute_request(request), {'result': 'complete'})
        read.assert_called_once_with(self.source, page_numbers=[1])
        self.assertEqual(request, before)

    def test_worker_rejects_unknown_request_protocol(self):
        for request in ({}, {'schema_version': 99}):
            with self.assertRaisesRegex(ValueError, 'request schema'):
                execute_request(request)

    def worker_request(self):
        request = {'schema_version': 1, 'source_path': str(self.source),
                   'engine': asdict(OcrSettings()), 'document': asdict(DocumentOcrSettings()),
                   'page_numbers': [1]}
        path = self.root / 'worker-request.json'
        path.write_text(json.dumps(request), encoding='utf-8')
        return path

    def test_worker_main_writes_complete_json_without_development_scripts(self):
        from parsing.ocr.documentOcrWorker import main
        request = self.worker_request()
        output = self.root / 'document.json'
        document = self.read()
        with patch('parsing.ocr.documentOcrWorker.execute_request', return_value=document):
            with patch.dict(sys.modules, {'scripts.ocr': None}):
                self.assertEqual(main(['--request', str(request), '--output', str(output)]), 0)
        self.assertEqual(json.loads(output.read_text(encoding='utf-8')), document)

    def test_worker_recognition_failure_does_not_publish_output(self):
        from parsing.ocr.documentOcrWorker import main
        request = self.worker_request()
        output = self.root / 'document.json'
        output.write_text('existing', encoding='utf-8')
        with patch('parsing.ocr.documentOcrWorker.execute_request', side_effect=RuntimeError('recognition failed')):
            with self.assertRaisesRegex(RuntimeError, 'recognition failed'):
                main(['--request', str(request), '--output', str(output)])
        self.assertEqual(output.read_text(encoding='utf-8'), 'existing')

    def test_worker_output_cannot_replace_source(self):
        from parsing.ocr.documentOcrWorker import main
        request = self.worker_request()
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, 'differ from input'):
            main(['--request', str(request), '--output', str(self.source)])
        self.assertEqual(self.source.read_bytes(), before)






if __name__ == '__main__':
    unittest.main()
