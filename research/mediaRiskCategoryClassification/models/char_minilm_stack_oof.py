"""Two complementary text views; per-label meta LR and nested Train cutoffs."""
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier
from models.char_tfidf_svm import build as build_char
from models._semantic import FrozenTextEncoder
from models._nested_stack import NestedTextStack

DESCRIPTION = 'Char SVM + frozen MiniLM/LR -> per-label LR stack; nested Train-only OOF cutoffs'
SOURCE_FILES = ('models/char_tfidf_svm.py', 'models/_semantic.py',
                'models/_nested_stack.py', 'models/_oof_thresholds.py')


def build(seed=42):
    return NestedTextStack(
        build_char(seed),
        FrozenTextEncoder('sentence-transformers/all-MiniLM-L6-v2',
                          '1110a243fdf4706b3f48f1d95db1a4f5529b4d41', 256),
        OneVsRestClassifier(LogisticRegression(C=1.0, class_weight='balanced',
            solver='liblinear', max_iter=5000, random_state=seed), n_jobs=1),
        random_state=seed)
