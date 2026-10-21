# Blind labelling instructions (PDG-84 text layer)

You label watchlist records for 15 risk labels. You see **only** the record texts, the taxonomy and the labelling
rules. You never see, and must not look for, any model output, reference label, score or previous decision.

## Inputs (read only these)

1. The batch file you are given (`cache/blind/<batch>.jsonl`): one record per line with `record_key` and `text`.
2. The taxonomy: `data/taxonomy/VV_Risk_Labels_Final_reviewed.xlsx`, sheet "Risk Labels" — column A label name,
   column C English definition / coverage / boundary, column E sample indicators. Read it with Python (openpyxl,
   read-only); never write to it.
3. `data/labelling_rules_pdg84.md` — the accepted rules for borderline cases. They override your own judgement.

Do not open anything else in the project (no `results/`, `cache/checker_*`, `data/dataset_900.jsonl`,
`data/labels_900_binary.csv`, `data/reference_review.csv`, notebooks or scripts).

## The 15 labels

PEP; Crime-Corruption; Crime-Fraud; Crime-Money Laundering; Crime-Trafficking & Smuggling; Crime-Cybercrime;
Crime-Violent Crime; Crime-Theft & Property Crime; Crime-Forgery & Counterfeiting; Crime-Tax Crime;
Crime-Organized Crime; Terrorism / Terrorist Financing; Proliferation / Proliferation Financing;
Human Rights / Atrocities; Judicial / Court.

## How to decide

* A label is **yes** only if the record's text supports it **for the listed person or entity**, following the label's
  definition, coverage and boundary and the rules. Otherwise **no**.
* Do not use outside knowledge about the person; judge only what the text says (common knowledge about what an
  organisation is, e.g. that ISIL is a terrorist group, is allowed).
* Programme names, the name of the authority that publishes the list, laws, and sentences about who listed the person
  are not evidence by themselves.
* The record text starts with `Source list:`; you may read it, but the list name alone is never evidence.

## Output

Write one CSV row per record and label (15 rows per record) to the output file you are given, with columns:

`record_key,label,answer,evidence,reason`

* `answer`: `yes` or `no`.
* `evidence`: for `yes`, the shortest exact quote from the text that supports it; empty for `no`.
* `reason`: one short line (for `no`, only when the label was a close call; otherwise empty).

Use proper CSV quoting. Write the whole file before finishing, then report the number of rows written.
