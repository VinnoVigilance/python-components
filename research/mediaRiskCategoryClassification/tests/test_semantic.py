"""Token coverage, article aggregation and persistence without model downloads."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from sklearn.base import clone
from models._semantic import FrozenTextEncoder, normalize_rows, token_windows


class TinyTokenizer:
    cls_token_id = 101
    sep_token_id = 102

    def encode(self, text, **kwargs):
        return [8, 9] if text == 'query: ' else [int(x) for x in text.split()]

    def num_special_tokens_to_add(self, pair=False):
        return 2

class SemanticTests(unittest.TestCase):
    def encoder(self, prefix='', max_length=5):
        encoder = FrozenTextEncoder('test', 'a' * 40, max_length, prefix=prefix,
                                    batch_size=2)
        encoder._tokenizer = TinyTokenizer()
        encoder._load = lambda: None
        encoder.n_features_out_ = 2
        encoder.fitted_ = True
        encoder.encoding_passes_ = []
        return encoder

    def test_windows_cover_full_tail_without_overlap(self):
        source = list(range(1001))
        windows = list(token_windows(source, 254))
        self.assertEqual([n for chunk in windows for n in chunk], source)
        self.assertEqual([len(chunk) for chunk in windows], [254, 254, 254, 239])
        self.assertEqual(list(token_windows([], 3)), [[]])
        self.assertEqual(list(token_windows([1, 2, 3], 3)), [[1, 2, 3]])
        with self.assertRaises(ValueError):
            list(token_windows([1], 0))

    def test_prefix_and_special_tokens_share_budget(self):
        encoder = self.encoder(prefix='query: ', max_length=7)
        chunks = list(encoder._iter_chunks(['1 2 3 4 5 6 7']))
        self.assertEqual([w for _, w, _, _ in chunks], [3, 3, 1])
        self.assertEqual([f['input_ids'][:3] for _, _, f, _ in chunks], [[101, 8, 9]] * 3)
        self.assertEqual([x for _, _, f, _ in chunks for x in f['input_ids'][3:-1]],
                         [1, 2, 3, 4, 5, 6, 7])
        self.assertTrue(all(len(f['input_ids']) <= 7 for _, _, f, _ in chunks))
        self.assertEqual([last for _, _, _, last in chunks], [False, False, True])

    def test_short_tail_weight_and_article_ownership(self):
        encoder = self.encoder()
        encoder._encode_batch = lambda fs: np.asarray([
            [1., 0.] if f['input_ids'][1] == 1 else [0., 1.] for f in fs])
        vectors = encoder.transform(['1 2 3 4', '5 6'])
        np.testing.assert_allclose(vectors, normalize_rows(np.array([[3., 1.], [0., 2.]])), rtol=1e-6)
        self.assertEqual(encoder.encoding_passes_[0],
                         {'articles': 2, 'chunks': 3, 'max_chunks_per_article': 2})

    def test_runtime_weights_are_not_pickled_and_clone_is_unfitted(self):
        encoder = FrozenTextEncoder('test', 'a' * 40, 256)
        encoder._backbone, encoder._torch, encoder._tokenizer = object(), object(), object()
        encoder.fitted_ = True
        state = encoder.__getstate__()
        self.assertTrue(state['fitted_'])
        self.assertFalse(any(key in state for key in ('_backbone', '_torch', '_tokenizer')))
        self.assertTrue(all(hasattr(encoder, key) for key in ('_backbone', '_torch', '_tokenizer')))
        self.assertFalse(hasattr(clone(encoder), 'fitted_'))


if __name__ == '__main__':
    unittest.main()
