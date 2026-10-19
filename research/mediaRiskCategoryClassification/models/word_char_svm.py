"""Combine word and character TF-IDF, normalize the joined vector and fit one SVM per label."""
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import Normalizer
from sklearn.svm import LinearSVC

DESCRIPTION = 'Word (1,2) + character within words (3,5) TF-IDF, equal weights, L2 normalization + balanced Linear SVM, C=1'


def build(seed=42):
    return Pipeline([
        ('features', FeatureUnion([
            ('word', TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_df=0.95,
                                     max_features=100000, sublinear_tf=True)),
            ('character', TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 5),
                                          min_df=2, max_df=0.95, max_features=100000,
                                          sublinear_tf=True)),
        ], n_jobs=1)),
        ('normalize', Normalizer(norm='l2', copy=False)),
        ('classifier', OneVsRestClassifier(
            LinearSVC(C=1.0, class_weight='balanced', max_iter=10000,
                      random_state=seed), n_jobs=1)),
    ])
