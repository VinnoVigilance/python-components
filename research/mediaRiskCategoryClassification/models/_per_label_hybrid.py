"""Fixed per-label routing of existing trained classifiers, without averaging."""
import numpy as np


def label_routes(labels):
    expected = {
        'Terrorism / Terrorist Financing', 'Regulatory', 'Law Enforcement', 'Judicial / Court',
        'Crime-Corruption', 'Crime-Fraud', 'Crime-Forgery & Counterfeiting', 'Crime-Cybercrime',
        'Crime-Trafficking & Smuggling', 'Crime-Violent Crime', 'Crime-Environmental & Wildlife Crime',
    }
    if len(labels) != 11 or set(labels) != expected:
        raise ValueError('hybrid_per_label_v1 requires the agreed 11-label scope.')
    routes = {label: 'base' for label in labels}
    routes['Judicial / Court'] = 'word_lr'
    for label in ('Crime-Corruption', 'Terrorism / Terrorist Financing', 'Crime-Cybercrime'):
        routes[label] = 'char_svm'
    return routes


class PerLabelHybrid:
    """Return threshold-centered scores; each label keeps its source decision."""

    def __init__(self, components, labels, routes):
        self.components = components
        self.labels = list(labels)
        self.routes = dict(routes)

    def decision_function(self, texts):
        texts = list(texts)
        scores = np.empty((len(texts), len(self.labels)), dtype=float)
        for key in dict.fromkeys(self.routes.values()):
            component = self.components[key]
            values = np.asarray(getattr(component['model'], component['score_method'])(texts), dtype=float)
            if values.shape != (len(texts), len(component['labels'])) or not np.isfinite(values).all():
                raise ValueError(f'{key}: invalid source score matrix.')
            if component['score_method'] == 'predict_proba' and ((values < 0).any() or (values > 1).any()):
                raise ValueError(f'{key}: probabilities outside [0, 1].')
            for index, label in enumerate(self.labels):
                if self.routes[label] == key:
                    source_index = component['labels'].index(label)
                    scores[:, index] = values[:, source_index] - component['thresholds'][source_index]
        return scores
