"""Two-member per-label stacking, with nested Train-only threshold predictions."""
import time

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, clone
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import KFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.utils.validation import check_is_fitted

from config import LABELS
from models._oof_thresholds import choose_f1_threshold


def checked_scores(estimator, X, rows):
    scores = np.asarray(estimator.decision_function(X), dtype=float)
    if scores.shape != (rows, len(LABELS)) or not np.isfinite(scores).all():
        raise ValueError(f'Expected finite N x {len(LABELS)} base decision margins.')
    return scores


class NestedTextStack(ClassifierMixin, BaseEstimator):
    """Fresh TF-IDF in every fold; only deterministic frozen vectors are shared.

    Each outer holdout is excluded from ALL supervised steps that predict it:
    inner base fits, meta fits/scaling, and the outer base refits. Outer scores
    select cutoffs. A separate full-Train inner cross-fit trains the final meta
    models, followed by full-Train base refits. No Validation/Test enters fit.
    """
    def __init__(self, lexical, encoder, semantic, cv=3, random_state=42,
                 meta_c=1.0, torch_threads=4):
        self.lexical = lexical
        self.encoder = encoder
        self.semantic = semantic
        self.cv = cv
        self.random_state = random_state
        self.meta_c = meta_c
        self.torch_threads = torch_threads

    @staticmethod
    def _classes(y):
        count = y.sum(axis=0)
        if ((count == 0) | (count == len(y))).any():
            raise ValueError('A training fold is missing a target class.')

    def _folds(self, n):
        return KFold(self.cv, shuffle=True, random_state=self.random_state).split(range(n))

    def _base_pair(self, texts, vectors, y):
        self._classes(y)
        lexical = clone(self.lexical).fit(texts, y)
        semantic = clone(self.semantic).fit(vectors, y)
        return lexical, semantic

    @staticmethod
    def _pair_scores(pair, texts, vectors):
        return np.stack((checked_scores(pair[0], texts, len(texts)),
                         checked_scores(pair[1], vectors, len(texts))), axis=2)

    def _inner_oof(self, texts, vectors, y, scope):
        oof = np.full((len(y), len(LABELS), 2), np.nan)
        seen = np.zeros(len(y), dtype=int)
        for fold, (fit, hold) in enumerate(self._folds(len(y)), 1):
            print(f'{scope} / inner {fold}/{self.cv}: '
                  f'fit {len(fit)}, score {len(hold)}', flush=True)
            pair = self._base_pair([texts[i] for i in fit], vectors[fit], y[fit])
            oof[hold] = self._pair_scores(pair, [texts[i] for i in hold], vectors[hold])
            seen[hold] += 1
        if not np.isfinite(oof).all() or not np.all(seen == 1):
            raise ValueError('Incomplete or overlapping inner cross-fit predictions.')
        return oof

    def _fit_meta(self, base_oof, y):
        self._classes(y)
        return [make_pipeline(StandardScaler(), LogisticRegression(
                    C=self.meta_c, solver='liblinear', max_iter=5000,
                    random_state=self.random_state)).fit(base_oof[:, j, :], y[:, j])
                for j in range(len(LABELS))]

    @staticmethod
    def _meta_scores(models, base_scores):
        scores = np.column_stack([m.decision_function(base_scores[:, j, :])
                                  for j, m in enumerate(models)])
        if not np.isfinite(scores).all():
            raise ValueError('Non-finite stack scores.')
        return scores

    def fit(self, X, y):
        if isinstance(X, str):
            raise ValueError('Pass a list of article strings.')
        texts, y = list(X), np.asarray(y)
        if y.shape != (len(texts), len(LABELS)) or not np.isin(y, [0, 1]).all():
            raise ValueError(f'Expected aligned text and N x {len(LABELS)} binary targets.')
        if not 2 <= self.cv <= len(y) or self.meta_c <= 0:
            raise ValueError('Invalid cv or meta_c.')
        self._classes(y)
        self.encoder_ = clone(self.encoder).set_params(torch_threads=self.torch_threads)
        # The pretrained, frozen encoder receives NO targets and learns no text
        # statistics. Its deterministic vectors can be shared across all folds.
        self.encoder_.fit(texts)
        vectors = self.encoder_.transform(texts)
        self.oof_scores_ = np.full(y.shape, np.nan)
        self.oof_fold_ids_ = np.zeros(len(y), dtype=int)
        folds = []
        for fold, (fit, hold) in enumerate(self._folds(len(y)), 1):
            started = time.perf_counter()
            scope = f'Outer {fold}/{self.cv}'
            print(f'{scope}: threshold holdout {len(hold)} articles', flush=True)
            fit_text = [texts[i] for i in fit]
            base_oof = self._inner_oof(fit_text, vectors[fit], y[fit], scope)
            meta = self._fit_meta(base_oof, y[fit])
            pair = self._base_pair(fit_text, vectors[fit], y[fit])
            scores = self._pair_scores(pair, [texts[i] for i in hold], vectors[hold])
            self.oof_scores_[hold] = self._meta_scores(meta, scores)
            self.oof_fold_ids_[hold] = fold
            folds.append({'fold': fold, 'fit_rows': len(fit), 'holdout_rows': len(hold),
                          'fit_positive_support': y[fit].sum(axis=0).tolist(),
                          'seconds': time.perf_counter() - started})
            print(f'{scope} finished in {folds[-1]["seconds"]:.2f} s', flush=True)
        if not np.isfinite(self.oof_scores_).all() or (self.oof_fold_ids_ == 0).any():
            raise ValueError('Incomplete outer cross-fit predictions.')
        self.threshold_report_ = []
        for j in range(len(LABELS)):
            cutoff, default, selected = choose_f1_threshold(y[:, j], self.oof_scores_[:, j])
            self.threshold_report_.append({
                'threshold': cutoff, 'default_threshold': 0.0,
                'train_support': int(y[:, j].sum()),
                'oof_f1_default': default, 'oof_f1_selected': selected})
        self.decision_thresholds_ = np.array([r['threshold'] for r in self.threshold_report_])
        self.threshold_policy_ = (f'Per-label F1 cutoffs from nested {self.cv}x{self.cv} '
                                  'Train cross-fit; no Validation/Test tuning')
        self.tuning_summary_ = {
            'source_split': 'train', 'outer_cv': self.cv, 'inner_cv': self.cv,
            'random_state': self.random_state, 'objective': 'per-label F1',
            'outer_holdout_excluded_from': ['TF-IDF vocabulary and IDF', 'SVM fit',
                'semantic LR fit', 'inner cross-fit', 'meta scaler and LR fit'],
            'frozen_encoder_policy': 'pretrained deterministic vectors; no targets or learned text statistics',
            'oof_score_note': 'used to select thresholds; not an independent quality estimate',
            'oof_macro_f1_default': float(np.mean([r['oof_f1_default'] for r in self.threshold_report_])),
            'oof_macro_f1_selected': float(np.mean([r['oof_f1_selected'] for r in self.threshold_report_])),
            'folds': folds}
        print('Final stack: fresh full-Train base cross-fit for meta training...', flush=True)
        self.base_train_oof_ = self._inner_oof(texts, vectors, y, 'Final Train')
        self.meta_ = self._fit_meta(self.base_train_oof_, y)
        print('Refitting both base classifiers on all Train articles...', flush=True)
        self.lexical_, self.semantic_ = self._base_pair(texts, vectors, y)
        self.classes_ = np.arange(len(LABELS))
        self.training_summary_ = {
            'train_articles': len(y), 'all_zero_target_rows': int((y.sum(axis=1) == 0).sum()),
            'encoder_frozen': True, 'validation_or_test_used_for_fit': False,
            'encoder': self.encoder_.runtime_metadata(),
            'meta_features_per_label': ['same-label raw SVM margin', 'same-label raw semantic LR margin'],
            'meta_policy': f'{len(LABELS)} separate StandardScaler + unweighted L2 LR, C=1; no class weighting',
            'base_pair_fits': (self.cv + 1) ** 2,
            'standardized_meta_coefficients': [m[-1].coef_[0].tolist() for m in self.meta_],
            'meta_intercepts': [float(m[-1].intercept_[0]) for m in self.meta_],
            'threshold_selection': self.tuning_summary_}
        return self

    def decision_function(self, X):
        check_is_fitted(self, 'meta_')
        if isinstance(X, str):
            raise ValueError('Pass a list of article strings.')
        texts = list(X)
        self.encoder_.set_params(torch_threads=self.torch_threads)
        vectors = self.encoder_.transform(texts)
        self.last_base_scores_ = self._pair_scores((self.lexical_, self.semantic_), texts, vectors)
        return self._meta_scores(self.meta_, self.last_base_scores_)

    def predict(self, X):
        return (self.decision_function(X) >= self.decision_thresholds_).astype(np.int8)

    def __getstate__(self):
        state = self.__dict__.copy()
        state.pop('last_base_scores_', None)
        return state
