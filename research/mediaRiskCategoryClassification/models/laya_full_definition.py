"""Verbatim reviewed English definitions; preserve the checkpoint's 512-token context."""
from models.laya_zeroshot import LayaZeroShotClassifier

DESCRIPTION = ('Laya CPU FP32; full verbatim English definitions; 384-token question budget, '
               '128-token article windows, overlap 32, max positive score, cutoff 0.5')
SOURCE_FILES = ('models/laya_zeroshot.py', 'models/risk_questions.json', 'requirements-laya.txt')


def build(seed=42):
    return LayaZeroShotClassifier(window_tokens=128, overlap_tokens=32,
                                 full_definition=True, head_max_len=384)
