"""Copy to a public name such as char_svm.py; implement build(seed)."""
DESCRIPTION = 'Describe the exact model and its fixed settings'


def build(seed=42):
    """Return an unfitted sklearn-compatible estimator supporting fit and predict_proba or decision_function."""
    raise NotImplementedError('Implement the new model here. Keep data loading and evaluation in the shared runner.')
