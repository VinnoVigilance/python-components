"""Run the shared document reader in an explicitly configured Python runtime."""

import hashlib
import json
import math
import os
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKER_MODULE = 'parsing.ocr.documentOcrWorker'


def validate_runtime(config):
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError('parser.runtime must be an object.')
    unknown = set(config) - {'mode', 'python_env', 'timeout_seconds', 'max_output_bytes'}
    if unknown:
        raise ValueError(f'Unknown OCR runtime settings: {sorted(unknown)}')
    mode = config.get('mode', 'in_process')
    if mode not in ('in_process', 'subprocess'):
        raise ValueError('parser.runtime.mode must be in_process or subprocess.')
    env_name = config.get('python_env', 'VV_OCR_PYTHON')
    if not isinstance(env_name, str) or not env_name or not env_name.replace('_', '').isalnum() or env_name[0].isdigit():
        raise ValueError('parser.runtime.python_env must name an environment variable.')
    timeout = config.get('timeout_seconds', 180)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('parser.runtime.timeout_seconds must be finite and positive.')
    limit = config.get('max_output_bytes', 128 * 1024 * 1024)
    if type(limit) is not int or limit < 1:
        raise ValueError('parser.runtime.max_output_bytes must be a positive integer.')
    return dict(mode=mode, python_env=env_name, timeout_seconds=timeout, max_output_bytes=limit)


def read_document_in_subprocess(source_path, engine_settings, document_settings, page_numbers, runtime):
    """Return one complete document or raise; never silently change runtime."""
    runtime = validate_runtime(runtime)
    if runtime['mode'] != 'subprocess':
        raise ValueError('Subprocess OCR requires mode=subprocess.')
    executable = os.environ.get(runtime['python_env'], '').strip()
    if not executable:
        raise RuntimeError(f"Set {runtime['python_env']} to the OCR environment's Python executable.")
    executable = Path(executable).expanduser()
    if not executable.is_absolute() or not executable.is_file():
        raise RuntimeError(f"{runtime['python_env']} must be an existing absolute Python executable path.")

    # Fingerprint the exact source before the worker runs. The worker takes its
    # own immutable snapshot; reject results if those bytes differ.
    source_path = Path(source_path).resolve()
    fingerprint, size = hashlib.sha256(), 0
    with source_path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > document_settings.max_source_bytes:
                raise ValueError('Source exceeds parser.document.max_source_bytes.')
            fingerprint.update(chunk)
    digest = fingerprint.hexdigest()
    request = {
        'schema_version': 1, 'source_path': str(source_path),
        'engine': asdict(engine_settings), 'document': asdict(document_settings),
        'page_numbers': list(page_numbers) if page_numbers is not None else None,
    }
    child_env = os.environ.copy()
    child_env['PYTHONPATH'] = str(PROJECT_ROOT) + (
        os.pathsep + child_env['PYTHONPATH'] if child_env.get('PYTHONPATH') else ''
    )
    child_env.pop('PYTHONHOME', None)
    with tempfile.TemporaryDirectory(prefix='vv_ocr_runtime_') as temp:
        directory = Path(temp).resolve()
        request_path = directory / 'request.json'
        output_path = directory / 'document.json'
        log_path = directory / 'worker.log'
        request_path.write_text(json.dumps(request, allow_nan=False), encoding='utf-8')
        command = [str(executable), '-u', '-m', WORKER_MODULE,
                   '--request', str(request_path), '--output', str(output_path)]
        try:
            with log_path.open('wb') as log:
                completed = subprocess.run(
                    command, cwd=PROJECT_ROOT, env=child_env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    timeout=runtime['timeout_seconds'], check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"OCR worker exceeded {runtime['timeout_seconds']} seconds.") from exc
        if completed.returncode:
            with log_path.open('rb') as log:
                log.seek(max(0, log_path.stat().st_size - 4000))
                detail = log.read(4000).decode('utf-8', errors='replace')
            raise RuntimeError(f'OCR worker failed (exit {completed.returncode}).\n{detail}')
        if not output_path.is_file():
            raise RuntimeError('OCR worker returned no document.')
        if output_path.stat().st_size > runtime['max_output_bytes']:
            raise ValueError('OCR worker output exceeds parser.runtime.max_output_bytes.')
        document = json.loads(output_path.read_text(encoding='utf-8'))
    if not isinstance(document, dict) or document.get('schema_version') != 1:
        raise ValueError('OCR worker returned an invalid document schema.')
    if document.get('source_sha256') != digest or document.get('source_size_bytes') != size:
        raise ValueError('OCR worker source hash/size does not match the input file.')
    if page_numbers is not None and document.get('selected_pages') != list(page_numbers):
        raise ValueError('OCR worker selected pages do not match parser.page_numbers.')
    document['source_path'] = str(source_path)
    return document
