"""Compress word TF-IDF into 300 latent dimensions before balanced logistic regression."""
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import Normalizer

DESCRIPTION = 'Word TF-IDF (1,2) + 300-dimensional LSA + L2 normalization + balanced Logistic Regression, C=1'


def build(seed=42):
    return Pipeline([
        ('tfidf', TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_df=0.95,
                                 max_features=100000, sublinear_tf=True)),
        ('lsa', TruncatedSVD(n_components=300, n_iter=7, random_state=seed)),
        ('normalize', Normalizer(norm='l2', copy=False)),
        ('classifier', OneVsRestClassifier(
            LogisticRegression(C=1.0, class_weight='balanced', solver='liblinear',
                               max_iter=5000, random_state=seed), n_jobs=1)),
    ])
