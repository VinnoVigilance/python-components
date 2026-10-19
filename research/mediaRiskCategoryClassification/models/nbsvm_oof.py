"""Word unigram/bigram NB-feature SVM with Train-only OOF cutoffs."""
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.pipeline import Pipeline

from models._nbsvm import NBSVMClassifier
from models._oof_thresholds import OOFThresholdClassifier

DESCRIPTION = 'Binary word (1,2)-grams + per-label NB log-count ratios + balanced SVM C=1 + Train OOF cutoffs'
SOURCE_FILES = ('models/_nbsvm.py', 'models/_oof_thresholds.py')


def build(seed=42):
    estimator = Pipeline([
        ('counts', CountVectorizer(binary=True, ngram_range=(1, 2), min_df=2,
                                   max_df=0.95, max_features=100000)),
        ('classifier', NBSVMClassifier(random_state=seed)),
    ])
    return OOFThresholdClassifier(estimator, cv=3, random_state=seed)
