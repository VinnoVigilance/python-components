"""Frozen Laya CPU decisions using reviewed definitions and full-article windows."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.utils.validation import check_is_fitted

from config import LABELS

MODEL_ID = 'convaiinnovations/laya'
REVISION = '7b928d828b7b0e022f929d9bd2e44165aa270148'
DESCRIPTION = (f'Frozen Laya English CPU FP32; {len(LABELS)} reviewed binary choice questions; '
               '256-token windows, 64-token overlap, maximum positive score, cutoff 0.5')
SOURCE_FILES = ('models/risk_questions.json', 'requirements-laya.txt')


def window_spans(length, size, overlap):
    """Cover every content token, including the final tail, with bounded overlap."""
    if size < 1 or not 0 <= overlap < size:
        raise ValueError('Expected positive window size and 0 <= overlap < size.')
    start = 0
    while start < length:
        end = min(start + size, length)
        yield start, end
        if end == length:
            break
        start = end - overlap


class LayaZeroShotClassifier(ClassifierMixin, BaseEstimator):
    supports_preview = True

    def __init__(self, model_id=MODEL_ID, revision=REVISION, window_tokens=256,
                 overlap_tokens=64, batch_size=1, torch_threads=4,
                 full_definition=False, head_max_len=None):
        self.model_id = model_id
        self.revision = revision
        self.window_tokens = window_tokens
        self.overlap_tokens = overlap_tokens
        self.batch_size = batch_size
        self.torch_threads = torch_threads
        self.full_definition = full_definition
        self.head_max_len = head_max_len

    def _read_questions(self):
        raw = Path(__file__).with_name('risk_questions.json').read_bytes()
        self.taxonomy_ = json.loads(raw)
        if [item['label'] for item in self.taxonomy_['labels']] != list(LABELS):
            raise ValueError('Laya question order must match config.LABELS exactly.')
        self.questions_sha256_ = hashlib.sha256(raw).hexdigest()
        self.questions_ = {
            item['stable_id']: {
                'type': 'choice',
                'instructions': self.taxonomy_['shared_instruction'] + ' ' + item['question'],
                'criteria': {'A': 'yes, ' + item['decision_concept'] + ' is reported',
                             'B': 'no, ' + item['decision_concept'] + ' is not reported'},
            } for item in self.taxonomy_['labels']}
        if getattr(self, 'full_definition', False):
            for item in self.taxonomy_['labels']:
                self.questions_[item['stable_id']]['instructions'] = (
                    self.taxonomy_['shared_instruction'] + '\n'
                    f"Category: {item['label']}\n"
                    + item['english_definition']
                    + '\nDoes the article meet this complete category definition?')

    def _load(self):
        try:
            import torch
            import laya
        except ImportError as exc:
            raise RuntimeError('Laya dependencies are missing. Use the .venv-laya '
                               'interpreter and requirements-laya.txt; see README.md.') from exc
        if version('laya') != '0.4.11':
            raise RuntimeError('This experiment requires laya==0.4.11 for reproducible inference.')
        torch.set_num_threads(self.torch_threads)
        if not hasattr(self, '_agent'):
            print(f'Loading frozen Laya CPU: {self.model_id} @ {self.revision[:12]}', flush=True)
            self._agent = laya.load(self.model_id, revision=self.revision,
                                    device='cpu', backend='eager')
            # Keep a predictable CPU FP32 path, independently of optional AMP env settings.
            self._agent.amp_enabled = False
            self._agent.dtype = torch.float32
            if getattr(self, 'head_max_len', None) is not None:
                self._agent.cfg['head_max_len'] = self.head_max_len
            tok = self._agent.tok
            head_limit = self._agent.cfg['head_max_len']
            for key, question in self.questions_.items():
                instruction = 'choice question: ' + question['instructions']
                options = [f'{k}: {v}' for k, v in question['criteria'].items()]
                option_cost = sum(1 + len(tok.encode(text, add_special_tokens=False)) for text in options)
                if len(tok.encode(instruction, add_special_tokens=False)) > head_limit - option_cost:
                    raise ValueError(f'Question {key} would be truncated; adjust the explicit token budget.')
                head_tokens = (len(tok.encode(instruction, add_special_tokens=False))
                               + option_cost + 3)
                if head_tokens + self.window_tokens + 8 > self._agent.cfg['max_len']:
                    raise ValueError(f'Question {key} leaves insufficient space for the complete article window.')
            if not getattr(tok, 'is_fast', False):
                raise ValueError('This full-text window policy requires a fast tokenizer with offsets.')

    def fit(self, X, y=None):
        if isinstance(X, str):
            raise ValueError('Pass a sequence of article strings.')
        texts = list(X)
        if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError('Expected nonempty article strings.')
        if y is not None and np.asarray(y).shape != (len(texts), len(LABELS)):
            raise ValueError(f'Expected one {len(LABELS)}-label target row per Train article.')
        if (self.torch_threads < 1 or self.batch_size < 1 or self.window_tokens > 256
                or self.window_tokens < 1 or not 0 <= self.overlap_tokens < self.window_tokens
                or len(self.revision) != 40
                or (getattr(self, 'head_max_len', None) is not None and self.head_max_len < 1)):
            raise ValueError('Invalid Laya CPU parameters.')
        self.__dict__.pop('_agent', None)
        self._read_questions()
        self._load()
        self.fitted_ = True
        self.threshold_policy_ = 'zero-shot; fixed score cutoff 0.5; no Train/Validation/Test tuning'
        self.training_summary_ = {
            'mode': 'zero-shot: pretrained weights and shipped temperatures are frozen',
            'train_articles_encoded': 0, 'train_targets_used_for_learning': False,
            'model_id': self.model_id, 'revision': self.revision, 'device': 'cpu',
            'precision': 'FP32; quantization disabled',
            'window_tokens': self.window_tokens, 'overlap_tokens': self.overlap_tokens,
            'aggregation': 'maximum A (positive) score across all windows, separately for each label',
            'score_interpretation': 'window-derived score; not calibrated document-level confidence',
            'question_type': 'independent two-option choice with neutral A/B keys',
            'definition_policy': 'verbatim English Description (Definition, Coverage, Boundary)'
                if getattr(self, 'full_definition', False) else 'compact paraphrase',
            'head_max_len_override': getattr(self, 'head_max_len', None),
            'context_tokens': 512,
            'questions_sha256': self.questions_sha256_,
            'taxonomy_source_sha256': self.taxonomy_['source_sha256'],
            'versions': {name: version(name) for name in ('laya', 'torch', 'transformers', 'huggingface-hub')},
        }
        return self

    def _windows(self, text):
        # The SDK sanitizes mask tokens too; use the same text for token offsets and scoring.
        text = text.replace(self._agent.tok.mask_token, ' ')
        encoded = self._agent.tok(text, add_special_tokens=False,
                                  return_offsets_mapping=True, truncation=False)
        offsets = encoded['offset_mapping']
        if not offsets:
            return [text]
        windows = []
        for start, end in window_spans(len(offsets), self.window_tokens, self.overlap_tokens):
            left = 0 if start == 0 else offsets[start][0]
            right = len(text) if end == len(offsets) else offsets[end - 1][1]
            windows.append(text[left:right])
        return windows

    def predict_proba(self, X):
        check_is_fitted(self, 'fitted_')
        if isinstance(X, str):
            raise ValueError('Pass a sequence of article strings.')
        texts = list(X)
        if any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError('Expected nonempty article strings.')
        if not texts:
            return np.empty((0, len(LABELS)))
        self._load()
        from tqdm import tqdm
        scores = np.empty((len(texts), len(LABELS)))
        keys = list(self.questions_)
        total_windows = 0
        self.last_window_counts_ = []
        with tqdm(total=len(texts), desc='Laya scoring', unit='article', miniters=0) as progress:
            for i, text in enumerate(texts):
                windows = self._windows(text)
                best = np.zeros(len(LABELS))
                for start in range(0, len(windows), self.batch_size):
                    batch = windows[start:start + self.batch_size]
                    results = self._agent.predict_batch(batch, self.questions_, batch_size=self.batch_size)
                    if len(results) != len(batch):
                        raise ValueError('Laya returned an unexpected number of window results.')
                    for result in results:
                        usage = result['usage']
                        if usage.get('truncated') or usage.get('state_tokens_dropped', 0):
                            raise ValueError('Laya dropped window content; refusing an incomplete score.')
                        answers = result['answers']
                        values = np.array([answers[key]['probabilities']['A'] for key in keys], dtype=float)
                        if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
                            raise ValueError('Expected one finite [0,1] score per label.')
                        best = np.maximum(best, values)
                    total_windows += len(batch)
                    progress.set_postfix(windows=total_windows, refresh=True)
                scores[i] = best
                self.last_window_counts_.append(len(windows))
                progress.update(1)
        return scores

    def __getstate__(self):
        state = self.__dict__.copy()
        state.pop('_agent', None)
        return state

    def __setstate__(self, state):
        # Bundles from the original compact experiment predate these parameters.
        state.setdefault('full_definition', False)
        state.setdefault('head_max_len', None)
        super().__setstate__(state)


def build(seed=42):
    return LayaZeroShotClassifier()
