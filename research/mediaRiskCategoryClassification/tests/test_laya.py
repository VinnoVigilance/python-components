"""Offline checks for the Laya article/label contract; no weight downloads."""
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from io import StringIO

import joblib
import numpy as np
from sklearn.base import clone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import LABELS
from models.laya_zeroshot import LayaZeroShotClassifier, window_spans


class WordTokenizer:
    mask_token = '[MASK]'

    def __call__(self, text, **kwargs):
        return {'offset_mapping': [(m.start(), m.end()) for m in re.finditer(r'\S+', text)]}


class FakeAgent:
    def __init__(self, dropped=False):
        self.tok = WordTokenizer()
        self.calls = 0
        self.dropped = dropped

    def predict_batch(self, states, questions, **kwargs):
        self.calls += 1
        return [
            {'usage': {'state_tokens_dropped': int(self.dropped)},
             'answers': {key: {'probabilities': {'A': 0.9 if key == 'CR_FRAUD' and 'tailfraud' in text else 0.1}}
                         for key in questions}}
            for text in states]


class LayaTests(unittest.TestCase):
    def test_full_definition_is_inserted_verbatim_for_every_active_label(self):
        from models.laya_full_definition import build
        model = build()
        model._read_questions()
        for item in model.taxonomy_['labels']:
            instructions = model.questions_[item['stable_id']]['instructions']
            self.assertIn(item['english_definition'], instructions)
            self.assertEqual(instructions.count(item['english_definition']), 1)
            self.assertTrue(instructions.endswith('Does the article meet this complete category definition?'))
        self.assertEqual(len(model.questions_), len(LABELS))

    def test_compact_control_matches_all_other_experiment_settings(self):
        from models.laya_full_definition import build as full
        from models.laya_compact_control import build as control
        left, right = full(), control()
        a, b = left.get_params(), right.get_params()
        self.assertTrue(a.pop('full_definition'))
        self.assertFalse(b.pop('full_definition'))
        self.assertEqual(a, b)
        self.assertEqual(a['window_tokens'], 128)
        self.assertEqual(a['head_max_len'], 384)
        left._read_questions()
        right._read_questions()
        for key in left.questions_:
            self.assertEqual(left.questions_[key]['criteria'], right.questions_[key]['criteria'])

    def test_original_compact_bundle_remains_compatible(self):
        original, _ = self.fit_model()
        state = original.__getstate__()
        state.pop('full_definition')
        state.pop('head_max_len')
        restored = LayaZeroShotClassifier.__new__(LayaZeroShotClassifier)
        restored.__setstate__(state)
        restored.set_params(torch_threads=2)
        self.assertFalse(restored.full_definition)
        self.assertIsNone(restored.head_max_len)
        self.assertEqual(restored.questions_, original.questions_)

    def fit_model(self, targets=None, agent=None):
        agent = agent or FakeAgent()
        model = LayaZeroShotClassifier()
        def load(instance):
            instance._agent = agent
        with patch.object(LayaZeroShotClassifier, '_load', load), \
             patch('models.laya_zeroshot.version', return_value='test'):
            model.fit(['ordinary train', 'another article'], targets)
        return model, agent

    def test_no_target_learning_and_exact_label_order(self):
        left, agent = self.fit_model(np.zeros((2, len(LABELS))))
        right, _ = self.fit_model(np.ones((2, len(LABELS))))
        self.assertEqual(agent.calls, 0)
        self.assertEqual([r['label'] for r in left.taxonomy_['labels']], list(LABELS))
        self.assertEqual(len(left.questions_), len(LABELS))
        self.assertEqual(left.questions_, right.questions_)
        self.assertFalse(left.training_summary_['train_targets_used_for_learning'])
        self.assertEqual(left.training_summary_['train_articles_encoded'], 0)
        self.assertFalse(hasattr(clone(left), 'fitted_'))

    def test_tail_signal_survives_and_empty_input(self):
        model, agent = self.fit_model()
        text = 'ordinary ' * 300 + 'tailfraud'
        with patch.object(model, '_load'):
            scores = model.predict_proba([text, 'ordinary article'])
            empty = model.predict_proba([])
        self.assertEqual(scores.shape, (2, len(LABELS)))
        fraud = list(LABELS).index('Crime-Fraud')
        self.assertEqual(scores[0, fraud], 0.9)
        self.assertEqual(scores[1, fraud], 0.1)
        self.assertEqual(model.last_window_counts_, [2, 1])
        self.assertEqual(empty.shape, (0, len(LABELS)))
        covered = set()
        for start, end in window_spans(1001, 256, 64):
            covered.update(range(start, end))
            self.assertLessEqual(end - start, 256)
        self.assertEqual(covered, set(range(1001)))

    def test_refuse_sdk_content_truncation(self):
        model, _ = self.fit_model(agent=FakeAgent(dropped=True))
        with patch.object(model, '_load'), self.assertRaisesRegex(ValueError, 'dropped window content'):
            model.predict_proba(['an article'])

    def test_reload_preserves_questions_but_excludes_encoder(self):
        model, _ = self.fit_model()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'model.joblib'
            joblib.dump(model, path)
            restored = joblib.load(path)
        self.assertFalse(hasattr(restored, '_agent'))
        self.assertEqual(restored.questions_, model.questions_)
        self.assertEqual(restored.questions_sha256_, model.questions_sha256_)
        with patch.object(restored, '_load', side_effect=lambda: setattr(restored, '_agent', FakeAgent())):
            scores = restored.predict_proba(['tailfraud'])
        self.assertEqual(scores.shape, (1, len(LABELS)))
        self.assertEqual(scores[0, list(LABELS).index('Crime-Fraud')], 0.9)

    def test_cli_preview_never_loads_held_out_data_or_creates_a_run(self):
        import run
        rows = [{'news_id': 'one', 'title': 'First'}, {'news_id': 'two', 'title': 'Second'}]
        targets = np.array([[0] * len(LABELS), [1] * len(LABELS)])
        def only_train(name):
            self.assertEqual(name, 'train')
            return rows, ['ordinary article', 'tailfraud'], targets
        def load(instance):
            instance._agent = FakeAgent()
        with tempfile.TemporaryDirectory() as tmp:
            outputs = Path(tmp) / 'outputs'
            capture = StringIO()
            with patch.object(run, 'load_split', side_effect=only_train) as loader, \
                 patch.object(run, 'OUTPUT_DIR', outputs), \
                 patch.object(LayaZeroShotClassifier, '_load', load), \
                 patch('models.laya_zeroshot.version', return_value='test'), \
                 patch.object(sys, 'argv', ['run.py', '--model', 'laya_zeroshot', '--preview', '2']), \
                 redirect_stdout(capture):
                run.main()
            loader.assert_called_once_with('train')
            self.assertFalse(outputs.exists())
            self.assertIn('not a model-quality estimate', capture.getvalue())


if __name__ == '__main__':
    unittest.main()
