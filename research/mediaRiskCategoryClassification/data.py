"""Load fixed splits, verify integrity and expose only text and the active targets."""
import csv
import hashlib
import json

import numpy as np

from config import DATA_DIR, LABELS


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def manifest():
    content = json.loads((DATA_DIR / 'manifest.json').read_text(encoding='utf-8'))
    if content['labels'] != list(LABELS):
        raise ValueError('config.LABELS does not match the frozen dataset label order.')
    return content


def dataset_id():
    return sha256(DATA_DIR / 'manifest.json')


def load_split(split):
    if split not in ('train', 'validation', 'test'):
        raise ValueError(f'Unknown split: {split}')
    meta = manifest()
    path = DATA_DIR / f'{split}.csv'
    if sha256(path) != meta['files_sha256'][path.name]:
        raise ValueError(f'{path.name} changed. Restore the original data or create a new dataset version.')
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != meta['split_counts'][split]:
        raise ValueError(f'Wrong row count in {path.name}.')
    if len({r['news_id'] for r in rows}) != len(rows):
        raise ValueError(f'Duplicate news identifiers in {path.name}.')
    if any(r['split'] != split or not r['text'].strip() for r in rows):
        raise ValueError(f'Invalid split or empty text in {path.name}.')
    if any(r[label] not in ('0', '1') for r in rows for label in LABELS):
        raise ValueError('Targets must be binary 0/1 values.')
    targets = np.array([[int(r[label]) for label in LABELS] for r in rows], dtype=np.int8)
    return rows, [r['text'] for r in rows], targets


def check_separation(first, second):
    for field in ('news_id', 'body_hash'):
        if {r[field] for r in first} & {r[field] for r in second}:
            raise ValueError(f'Data leakage: overlapping {field} between splits.')
