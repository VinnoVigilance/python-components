"""Freeze checker v6 (general fixes after the v5 proof) so it can be proven on a future, untouched export."""
import hashlib
import json
from datetime import date
from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[1]
FROZEN = RESEARCH / "results" / "frozen_versions.json"
QWEN_8B = ["Crime-Fraud", "Judicial / Court"]


def sha(path):
    return hashlib.sha256((RESEARCH / path).read_bytes()).hexdigest()[:16]


def main():
    frozen = json.load(open(FROZEN, encoding="utf-8"))
    frozen["v6"] = {
        "frozen_on": str(date.today()),
        "reason": "general fixes after the v5 proof; no untouched rare-label data left in this export, so v6 waits for "
                  "a new export to be proven",
        "teacher_per_label": {label: "Qwen/Qwen3-8B" if label in QWEN_8B else "Qwen/Qwen3-4B-Instruct-2507"
                              for label in frozen["scope_labels"]},
        "quantisation": "4-bit NF4 (bitsandbytes), bfloat16 compute; chat template with enable_thinking=False",
        "prompt_version": "v6 (QUESTION_V6: part of a name is not evidence; NOTES_V6: PEP with candidates and sub-national "
                          "roles, Fraud, Organized Crime, Corruption, Tax added to the v5 notes)",
        "highlighter": "v5 + words naming the fight against a conduct skipped (flag_units version v6)",
        "lookup": "COMELEC-2025-SENATORS -> PEP (dataset category)",
        "cut_off": 0.5,
        "record_rule": "label if any flagged piece has P(yes) >= 0.5",
        "victim_rule": "no text-layer labels if Qwen3-4B P(victim) >= 0.5 (v4 role answers, unchanged)",
        "student": "intfloat/multilingual-e5-small fine-tuned on v6 answers (cache/student_e5small_v6)",
        "review_only_labels": ["Crime-Corruption", "Crime-Tax Crime"],
        "design_data": "v5 check pool, random 300 and held-out samples (used to design v6; not proof data)",
        "files_sha256_16": {p: sha(p) for p in ["scripts/checker.py", "scripts/highlighter.py", "scripts/lookupRules.py",
                                                 "data/labelling_rules_pdg84.md", "data/blind_labelling_instructions.md",
                                                 "cache/fresh/checker_answers_v6.csv", "cache/checker_pyes_v6.csv"]},
    }
    json.dump(frozen, open(FROZEN, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps(frozen["v6"], indent=2)[:400])


if __name__ == "__main__":
    main()
