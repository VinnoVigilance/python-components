"""Blend two saved SVM margin matrices using Train OOF statistics only."""
import numpy as np
from sklearn.metrics import f1_score
from sklearn.exceptions import NotFittedError

from models._oof_thresholds import choose_f1_threshold


class TrainOOFMarginBlend:
    """One global convex weight and one cutoff per label, selected on Train OOF.

    Per-label standardization aligns the two margin scales. Candidate weights
    are fixed in advance; all are selected on Train. The selected OOF F1 is a
    tuning diagnostic, not an independent quality estimate. No base retraining.
    """

    def __init__(self, nb_model, char_model):
        self.nb_model = nb_model
        self.char_model = char_model

    @staticmethod
    def _checked(values, shape):
        values = np.asarray(values, dtype=float)
        if values.shape != shape or not np.isfinite(values).all():
            raise ValueError(f'Expected finite scores with shape {shape}.')
        return values

    def fit_oof(self, nb_scores, char_scores, targets, nb_thresholds, char_thresholds):
        targets = np.asarray(targets)
        if targets.ndim != 2 or not len(targets) or not np.isin(targets, [0, 1]).all():
            raise ValueError('Expected binary Train targets.')
        nb = self._checked(nb_scores, targets.shape)
        char = self._checked(char_scores, targets.shape)
        nb_thresholds = self._checked(nb_thresholds, (targets.shape[1],))
        char_thresholds = self._checked(char_thresholds, (targets.shape[1],))
        self.classes_ = np.arange(targets.shape[1])
        self.nb_mean_, self.char_mean_ = nb.mean(axis=0), char.mean(axis=0)
        self.nb_scale_, self.char_scale_ = nb.std(axis=0), char.std(axis=0)
        if (self.nb_scale_ <= 1e-12).any() or (self.char_scale_ <= 1e-12).any():
            raise ValueError('A source label has constant Train OOF margins.')
        nb_z, char_z = self._combine_inputs(nb, char)
        candidates = []
        self.weight_search_ = []
        for weight in (0.0, 0.25, 0.5, 0.75, 1.0):
            scores = weight * nb_z + (1.0 - weight) * char_z
            if weight == 0.0:
                cutoffs = (char_thresholds - self.char_mean_) / self.char_scale_
            elif weight == 1.0:
                cutoffs = (nb_thresholds - self.nb_mean_) / self.nb_scale_
            else:
                cutoffs = np.array([choose_f1_threshold(targets[:, j], scores[:, j])[0]
                                    for j in range(targets.shape[1])])
            predictions = scores >= cutoffs
            macro = float(f1_score(targets, predictions, average='macro', zero_division=0))
            micro = float(f1_score(targets, predictions, average='micro', zero_division=0))
            self.weight_search_.append({'nb_weight': weight, 'char_weight': 1.0 - weight,
                                        'train_oof_macro_f1_selected': macro,
                                        'train_oof_micro_f1_selected': micro})
            candidates.append((macro, weight, cutoffs, scores))
        best = max(item[0] for item in candidates)
        tied = [item for item in candidates if np.isclose(item[0], best, rtol=0, atol=1e-12)]
        _, self.nb_weight_, self.decision_thresholds_, self.oof_scores_ = min(
            tied, key=lambda item: (abs(item[1] - 0.5), -item[1]))
        self.threshold_policy_ = ('Global NB/char weight from a fixed five-value grid and per-label '
                                  'F1 cutoffs selected on Train OOF only; no Validation/Test tuning')
        self.tuning_summary_ = {
            'source_split': 'train', 'nb_weight': self.nb_weight_, 'char_weight': 1.0 - self.nb_weight_,
            'weight_grid': [0.0, 0.25, 0.5, 0.75, 1.0], 'objective': 'Train OOF Macro-F1',
            'tie_policy': 'closest to equal weights, then larger NB weight',
            'standardization': 'Per-label mean and population std from Train OOF margins.',
            'endpoint_policy': 'Keep the original source thresholds for weight 0 or 1.',
            'oof_score_note': 'OOF scores select weights and cutoffs; not an independent quality estimate.',
            'selected_oof_macro_f1': best, 'base_models_refitted': False}
        self.threshold_report_ = []
        for index, cutoff in enumerate(self.decision_thresholds_):
            self.threshold_report_.append({
                'threshold': float(cutoff), 'default_threshold': 0.0,
                'train_support': int(targets[:, index].sum()),
                'oof_f1_default': float(f1_score(targets[:, index], self.oof_scores_[:, index] >= 0,
                                                zero_division=0)),
                'oof_f1_selected': float(f1_score(targets[:, index], self.oof_scores_[:, index] >= cutoff,
                                                 zero_division=0))})
        return self

    def _combine_inputs(self, nb, char):
        return ((nb - self.nb_mean_) / self.nb_scale_,
                (char - self.char_mean_) / self.char_scale_)

    def decision_function(self, X):
        if not hasattr(self, 'nb_weight_'):
            raise NotFittedError('Call fit_oof before scoring the blend.')
        if isinstance(X, str):
            raise ValueError('Pass a list of article strings.')
        texts = list(X)
        shape = (len(texts), len(self.classes_))
        if self.nb_weight_ == 0.0:
            char = self._checked(self.char_model.decision_function(texts), shape)
            return (char - self.char_mean_) / self.char_scale_
        if self.nb_weight_ == 1.0:
            nb = self._checked(self.nb_model.decision_function(texts), shape)
            return (nb - self.nb_mean_) / self.nb_scale_
        nb = self._checked(self.nb_model.decision_function(texts), shape)
        char = self._checked(self.char_model.decision_function(texts), shape)
        nb_z, char_z = self._combine_inputs(nb, char)
        return self.nb_weight_ * nb_z + (1.0 - self.nb_weight_) * char_z

    def predict(self, X):
        return (self.decision_function(X) >= self.decision_thresholds_).astype(np.int8)
