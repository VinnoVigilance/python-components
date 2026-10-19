"""Trainable fastText word/bigram embeddings with Train-only OOF thresholds."""
from models._fasttext import FastTextClassifier
from models._oof_thresholds import OOFThresholdClassifier

DESCRIPTION = 'CPU fastText: learned 50-d word/bigram vectors, OVA loss, 3-fold Train OOF cutoffs'
SOURCE_FILES = ('models/_fasttext.py', 'models/_oof_thresholds.py')


def build(seed=42):
    return OOFThresholdClassifier(FastTextClassifier(seed=seed), cv=3, random_state=seed)
