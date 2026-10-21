"""CPU fastText supervised classification with independent label scores."""
from pathlib import Path
import tempfile
import unicodedata

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.utils.validation import check_is_fitted


def clean_text(text):
    # Whitespace and reserved-token handling only; no source-specific rules.
    text = unicodedata.normalize('NFC', str(text)).lower()
    return ' '.join(text.replace('__label__', 'label_').replace('</s>', ' ').split())


def fasttext_module():
    try:
        import fasttext
    except ImportError as exc:
        raise ImportError('Install the native CPU package: python -m pip install '
                          '--only-binary=:all: "fasttext-wheel==0.9.2"') from exc
    return fasttext


class FastTextClassifier(ClassifierMixin, BaseEstimator):
    """Trainable word/bigram embeddings and one-vs-all output heads.

    All-zero articles receive an auxiliary NONE target so that fastText does
    not skip them as unlabeled lines. That auxiliary output is never returned
    as a risk label. Prediction reads Python scalar pairs from the pinned native
    binding, avoiding the old wrapper's incompatible NumPy 2 array conversion.
    """

    def __init__(self, dim=50, epoch=25, lr=0.3, word_ngrams=2, bucket=50000,
                 seed=42, threads=1):
        self.dim = dim
        self.epoch = epoch
        self.lr = lr
        self.word_ngrams = word_ngrams
        self.bucket = bucket
        self.seed = seed
        self.threads = threads

    def fit(self, X, y):
        if isinstance(X, str):
            raise ValueError('Pass a list of article strings.')
        texts, targets = list(X), np.asarray(y)
        if (targets.ndim != 2 or len(targets) != len(texts) or not len(texts)
                or not np.isin(targets, [0, 1]).all()):
            raise ValueError('Expected aligned article strings and binary multi-label targets.')
        support = targets.sum(axis=0)
        if ((support == 0) | (support == len(texts))).any():
            raise ValueError('Every risk label needs positive and negative training examples.')
        if min(self.dim, self.epoch, self.word_ngrams, self.bucket, self.threads) < 1:
            raise ValueError('Model sizes, epochs and threads must be positive.')
        fasttext = fasttext_module()
        self.label_codes_ = [f'__label__risk_{i:03d}' for i in range(targets.shape[1])]
        self.classes_ = np.arange(targets.shape[1])
        self.training_rows_ = len(texts)
        self.all_zero_training_rows_ = int((targets.sum(axis=1) == 0).sum())
        print(f'fastText CPU: {len(texts)} articles ({self.all_zero_training_rows_} all-zero retained), '
              f'epochs={self.epoch}, dim={self.dim}, native threads={self.threads}', flush=True)
        with tempfile.TemporaryDirectory(prefix='media_fasttext_') as tmp:
            directory = Path(tmp)
            path = directory / 'train.txt'
            with path.open('w', encoding='utf-8', newline='\n') as stream:
                for text, target in zip(texts, targets):
                    cleaned = clean_text(text)
                    if not cleaned:
                        raise ValueError('Empty article after whitespace normalization.')
                    labels = [code for code, value in zip(self.label_codes_, target) if value]
                    stream.write(' '.join(labels or ['__label__none']) + ' ' + cleaned + '\n')
            self.fasttext_model_ = fasttext.train_supervised(
                input=str(path), loss='ova', lr=self.lr, epoch=self.epoch, dim=self.dim,
                wordNgrams=self.word_ngrams, bucket=self.bucket, minCount=1,
                minn=0, maxn=0, thread=self.threads, seed=self.seed, verbose=2)
            if not set(self.label_codes_).issubset(self.fasttext_model_.get_labels()):
                raise ValueError('The native model is missing a risk output label.')
            model_path = directory / 'model.bin'
            self.fasttext_model_.save_model(str(model_path))
            self.model_binary_ = model_path.read_bytes()
        return self

    def predict_proba(self, X):
        check_is_fitted(self, 'fasttext_model_')
        if isinstance(X, str):
            raise ValueError('Pass a list of article strings.')
        texts = list(X)
        result = np.zeros((len(texts), len(self.label_codes_)), dtype=float)
        lookup = {code: i for i, code in enumerate(self.label_codes_)}
        for index, text in enumerate(texts):
            cleaned = clean_text(text)
            if not cleaned:
                raise ValueError('Empty article after whitespace normalization.')
            # Pinned fastText 0.9.2: this binding returns Python float/str pairs.
            # Its scalar Python wrapper fails with NumPy 2 (copy=False), while
            # its batch NumPy arrays can repeat the first probability. Avoid both
            # paths rather than changing NumPy or patching global package code.
            pairs = self.fasttext_model_.f.predict(cleaned + '\n', -1, 0.0, 'strict')
            seen = set()
            for value, code in pairs:
                if code in lookup:
                    result[index, lookup[code]] = value
                    seen.add(code)
            if seen != set(self.label_codes_):
                raise ValueError('The native predictor omitted a risk label.')
        if not np.isfinite(result).all() or (result < 0).any() or (result > 1.0001).any():
            raise ValueError('Invalid native fastText scores.')
        # Native fastText adds a tiny epsilon and can return values above 1.
        return np.clip(result, 0.0, 1.0)

    def decision_function(self, X):
        probabilities = np.clip(self.predict_proba(X), 1e-7, 1 - 1e-7)
        return np.log(probabilities) - np.log1p(-probabilities)

    def predict(self, X):
        return (self.decision_function(X) >= 0).astype(np.int8)

    def __getstate__(self):
        state = self.__dict__.copy()
        state.pop('fasttext_model_', None)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        if 'model_binary_' in state:
            with tempfile.TemporaryDirectory(prefix='media_fasttext_load_') as tmp:
                path = Path(tmp) / 'model.bin'
                path.write_bytes(self.model_binary_)
                self.fasttext_model_ = fasttext_module().load_model(str(path))
