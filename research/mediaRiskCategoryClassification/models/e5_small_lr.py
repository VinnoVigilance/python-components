"""Frozen English E5-small-v2 article embeddings plus balanced logistic regression."""
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier
from sklearn.pipeline import Pipeline
from models._semantic import FrozenTextEncoder

DESCRIPTION = 'Frozen English E5-small-v2 (CPU, query prefix, full-article token chunks) + balanced LR, C=1'
SOURCE_FILES = ('models/_semantic.py',)


def build(seed=42):
    return Pipeline([
        ('encoder', FrozenTextEncoder(
            model_id='intfloat/e5-small-v2',
            revision='ffb93f3bd4047442299a41ebb6fa998a38507c52',
            max_length=512, prefix='query: ')),
        ('classifier', OneVsRestClassifier(
            LogisticRegression(C=1.0, class_weight='balanced', solver='liblinear',
                               max_iter=5000, random_state=seed), n_jobs=1)),
    ])
