"""Word TF-IDF with a Complement Naive Bayes classifier per label."""
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.multiclass import OneVsRestClassifier
from sklearn.naive_bayes import ComplementNB
from sklearn.pipeline import Pipeline

DESCRIPTION = 'Word TF-IDF (1,2) + Complement Naive Bayes, alpha=1, norm=False'


def build(seed=42):
    return Pipeline([
        ('tfidf', TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_df=0.95,
                                 max_features=100000, sublinear_tf=True)),
        ('classifier', OneVsRestClassifier(ComplementNB(alpha=1.0, norm=False), n_jobs=1)),
    ])
