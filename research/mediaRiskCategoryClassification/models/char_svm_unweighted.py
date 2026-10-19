"""Change only the class weights of the existing character SVM baseline."""
from models.char_tfidf_svm import build as build_baseline

DESCRIPTION = 'Character TF-IDF (3,5) + Linear SVM, C=1, class_weight=None, threshold=0'
SOURCE_FILES = ('models/char_tfidf_svm.py',)


def build(seed=42):
    return build_baseline(seed).set_params(classifier__estimator__class_weight=None)
