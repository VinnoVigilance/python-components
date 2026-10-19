"""One NB log-count feature ratio and linear SVM per risk label."""
import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.svm import LinearSVC
from sklearn.utils.validation import check_array, check_is_fitted


class NBSVMClassifier(ClassifierMixin, BaseEstimator):
    """Binary n-gram features multiplied by label-specific NB log-count ratios.

    This is the NB-feature SVM variant (without coefficient interpolation).
    Ratios and SVMs are learned only in fit. The enclosing OOF pipeline refits
    the vocabulary, ratios and SVMs independently inside each Train fold.
    """

    def __init__(self, alpha=1.0, C=1.0, class_weight='balanced', max_iter=10000, random_state=42):
        self.alpha = alpha
        self.C = C
        self.class_weight = class_weight
        self.max_iter = max_iter
        self.random_state = random_state

    def fit(self, X, y):
        features = check_array(X, accept_sparse='csr', dtype=np.float64)
        targets = np.asarray(y)
        if (targets.ndim != 2 or targets.shape[0] != features.shape[0]
                or targets.shape[1] == 0 or not np.isin(targets, [0, 1]).all()):
            raise ValueError('Expected a binary multilabel target matrix aligned with the feature rows.')
        if self.alpha <= 0 or self.C <= 0:
            raise ValueError('alpha and C must be positive.')
        if features.data.size and not np.isin(features.data, [0, 1]).all():
            raise ValueError('NB-SVM expects binary occurrence features (CountVectorizer(binary=True)).')
        support = targets.sum(axis=0)
        if ((support == 0) | (support == len(targets))).any():
            raise ValueError('Each label needs positive and negative Train examples.')
        self.n_features_in_ = features.shape[1]
        self.classes_ = np.arange(targets.shape[1])
        self.estimators_, ratios = [], []
        total_counts = np.asarray(features.sum(axis=0)).ravel()
        for column in range(targets.shape[1]):
            positive_counts = np.asarray(features[targets[:, column] == 1].sum(axis=0)).ravel()
            positive = positive_counts + self.alpha
            negative = total_counts - positive_counts + self.alpha
            ratio = np.log(positive / positive.sum()) - np.log(negative / negative.sum())
            weighted = features.multiply(ratio).tocsr()
            estimator = LinearSVC(C=self.C, class_weight=self.class_weight, max_iter=self.max_iter,
                                  random_state=self.random_state)
            estimator.fit(weighted, targets[:, column])
            self.estimators_.append(estimator)
            ratios.append(ratio)
        self.log_count_ratios_ = np.vstack(ratios)
        return self

    def decision_function(self, X):
        check_is_fitted(self, ('estimators_', 'log_count_ratios_'))
        features = check_array(X, accept_sparse='csr', dtype=np.float64)
        if features.shape[1] != self.n_features_in_:
            raise ValueError('Feature vocabulary differs from the fitted NB-SVM vocabulary.')
        return np.column_stack([
            estimator.decision_function(features.multiply(ratio).tocsr())
            for estimator, ratio in zip(self.estimators_, self.log_count_ratios_)])

    def predict(self, X):
        return (self.decision_function(X) >= 0).astype(np.int8)
