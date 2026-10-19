"""Compact definitions under the same article/context budgets as the full-definition run."""
from models.laya_zeroshot import LayaZeroShotClassifier

DESCRIPTION = ('Laya CPU FP32; compact control; 384-token question budget, '
               '128-token article windows, overlap 32, max positive score, cutoff 0.5')
SOURCE_FILES = ('models/laya_zeroshot.py', 'models/risk_questions.json', 'requirements-laya.txt')


def build(seed=42):
    return LayaZeroShotClassifier(window_tokens=128, overlap_tokens=32,
                                 full_definition=False, head_max_len=384)
