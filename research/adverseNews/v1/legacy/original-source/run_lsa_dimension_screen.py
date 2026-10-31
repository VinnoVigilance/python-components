"""Check whether fewer or more LSA dimensions improve publisher transfer."""

from pathlib import Path

from run_expanded_screen import main, spec


if __name__ == '__main__':
    for dimensions in (50, 200):
        candidates = [spec(f'lsa{dimensions}_rbf_C{c:g}', 'lsa', 'rbf_svm', C=c)
                      for c in (1, 3, 10)]
        main(specs=candidates,
             output=Path(f'research/adverseNews/results/lsa{dimensions}-screen-v1'),
             lsa_components=dimensions)
