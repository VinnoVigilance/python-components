"""Refine the linear and low-rank families selected by the broad screen."""

from pathlib import Path

from run_expanded_screen import main, spec


REFINED = [
    *(spec(f'char_svm_C{c:g}', 'char', 'linear_svm', C=c) for c in (0.1, 0.3, 3, 10)),
    *(spec(f'hybrid_svm_C{c:g}', 'hybrid', 'linear_svm', C=c) for c in (0.1, 0.3, 3, 10)),
    *(spec(f'word_svm_C{c:g}', 'word', 'linear_svm', C=c) for c in (3, 10)),
    *(spec(f'lsa_rbf_C{c:g}', 'lsa', 'rbf_svm', C=c) for c in (0.3, 3, 10)),
    *(spec(f'lsa_logistic_C{c:g}', 'lsa', 'logistic', C=c) for c in (0.1, 3, 10)),
    *(spec(f'hybrid_nbsvm_C{c:g}', 'hybrid', 'nbsvm', C=c) for c in (0.1, 3)),
]


if __name__ == '__main__':
    main(specs=REFINED, output=Path('research/adverseNews/results/refined-screen-v1'))
