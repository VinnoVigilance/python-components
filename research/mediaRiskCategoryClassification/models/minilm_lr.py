"""Frozen all-MiniLM-L6-v2 article embeddings plus balanced logistic regression."""
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline
from models._semantic import FrozenTextEncoder

DESCRIPTION = 'Frozen MiniLM-L6-v2 (CPU, full-article token chunks) + balanced LR, C=1'
SOURCE_FILES = ('models/_semantic.py',)


def build(seed=42):
    return Pipeline([
        ('encoder', FrozenTextEncoder(
            model_id='sentence-transformers/all-MiniLM-L6-v2',
            revision='1110a243fdf4706b3f48f1d95db1a4f5529b4d41', max_length=256)),
        ('classifier', OneVsRestClassifier(
            LogisticRegression(C=1.0, class_weight='balanced', solver='liblinear',
                               max_iter=5000, random_state=seed), n_jobs=1)),
    ])
