"""Keep the balanced character SVM; learn only its decision thresholds via Train OOF."""
from models.char_tfidf_svm import build as build_baseline
from models._oof_thresholds import OOFThresholdClassifier

DESCRIPTION = 'Balanced character SVM C=1 + per-label F1 thresholds from 3-fold Train OOF'
SOURCE_FILES = ('models/char_tfidf_svm.py', 'models/_oof_thresholds.py')


def build(seed=42):
    return OOFThresholdClassifier(build_baseline(seed), cv=3, random_state=seed)
