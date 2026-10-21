"""Active-scope integrity and isolation from historical 14-label runs."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import LABELS
from data import load_split, manifest
from evaluation import score_model
import evaluate
import run


class ScopeTests(unittest.TestCase):
    def test_active_scope_and_all_article_rows_are_retained(self):
        self.assertEqual(len(LABELS), 11)
        removed = {'PEP', 'Crime-Organized Crime', 'Crime-Theft & Property Crime'}
        self.assertFalse(removed.intersection(LABELS))
        meta = manifest()
        self.assertEqual(meta['dataset_version'], 'vv-media-intersection-11-v1')
        for split, count in [('train', 3396), ('validation', 728), ('test', 727)]:
            rows, texts, targets = load_split(split)
            self.assertEqual(targets.shape, (count, 11))
            self.assertFalse(removed.intersection(rows[0]))
            self.assertEqual(int((targets.sum(axis=1) == 0).sum()), meta['all_zero_active_target_rows'][split])
            for row, target in zip(rows, targets):
                expected = ', '.join(label for label, value in zip(LABELS, target) if value)
                self.assertEqual(row['selected_labels'], expected)
                self.assertEqual(int(row['selected_label_count']), int(target.sum()))

    def test_old_fourteen_column_scores_are_rejected(self):
        class OldModel:
            def decision_function(self, texts):
                return np.zeros((len(texts), 14))
        with self.assertRaisesRegex(ValueError, 'shape'):
            score_model(OldModel(), ['one article'])

    def test_old_comparison_rows_are_removed_without_mutating_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = Path(tmp)
            old = outputs / 'historical_14'
            old.mkdir()
            meta = {'status': 'completed', 'dataset_id': 'old14', 'protocol_id': 'old14',
                    'labels': ['PEP', *LABELS]}
            source = json.dumps(meta)
            (old / 'run.json').write_text(source)
            (old / 'validation_summary.json').write_text('{}')
            (outputs / 'model_comparison.csv').write_text('stale,14-label,index\n')
            with patch.object(run, 'OUTPUT_DIR', outputs):
                self.assertEqual(run.refresh_comparison(), [])
            with (outputs / 'model_comparison.csv').open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.DictReader(stream)
                self.assertIn('label_count', reader.fieldnames)
                self.assertEqual(list(reader), [])
            self.assertEqual((old / 'run.json').read_text(), source)
            self.assertEqual((old / 'validation_summary.json').read_text(), '{}')

    def test_old_model_is_rejected_before_unpickling(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'run.json').write_text(json.dumps({
                'status': 'completed', 'labels': ['PEP', *LABELS],
                'dataset_id': 'old14', 'protocol_id': 'old14'}))
            with patch.object(sys, 'argv', ['evaluate.py', '--run', str(directory)]), \
                 patch.object(evaluate.joblib, 'load') as loader:
                with self.assertRaisesRegex(ValueError, 'Train a new 11-label run'):
                    evaluate.main()
                loader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
