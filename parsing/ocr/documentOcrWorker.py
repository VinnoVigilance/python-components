"""Private JSON-file protocol for document OCR; no pipeline/DB imports."""

import argparse
import json
from pathlib import Path


def execute_request(request):
    from parsing.ocr.documentOcrReader import DocumentOcrReader, DocumentOcrSettings
    from parsing.ocr.rapidOcrEngine import OcrSettings, RapidOcrEngine

    required = {'schema_version', 'source_path', 'engine', 'document', 'page_numbers'}
    if not isinstance(request, dict) or set(request) != required or request['schema_version'] != 1:
        raise ValueError('Invalid OCR worker request schema.')
    source = request['source_path']
    if not isinstance(source, str) or not Path(source).is_absolute():
        raise ValueError('OCR worker source_path must be absolute.')
    if not isinstance(request['engine'], dict) or not isinstance(request['document'], dict):
        raise ValueError('OCR worker engine/document settings must be objects.')
    engine = RapidOcrEngine(OcrSettings(**request['engine']))
    reader = DocumentOcrReader(engine, DocumentOcrSettings(**request['document']))
    return reader.read(Path(source).resolve(), page_numbers=request['page_numbers'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    request_path, output = Path(args.request).resolve(), Path(args.output).resolve()
    request = json.loads(request_path.read_text(encoding='utf-8'))
    if output == request_path or output == Path(request['source_path']).resolve():
        raise ValueError('Worker output must differ from input paths.')
    document = execute_request(request)
    payload = json.dumps(document, ensure_ascii=False, allow_nan=False)
    output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
