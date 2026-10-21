"""Freeze checker v5 before its new check pool is drawn: teacher per label, code, rules and answer files by hash."""
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
    frozen["v5"] = {
        "frozen_on": str(date.today()),
        "reason": "frozen before the v5 check pool is drawn from never-inspected training records",
        "teacher_per_label": {label: "Qwen/Qwen3-8B" if label in QWEN_8B else "Qwen/Qwen3-4B-Instruct-2507"
                              for label in frozen["scope_labels"]},
        "quantisation": "4-bit NF4 (bitsandbytes), bfloat16 compute; chat template with enable_thinking=False",
        "prompt_version": "v5 (NOTES_V5: PEP, Violent, Trafficking, Judicial, Money Laundering, Human Rights)",
        "highlighter": "v1 + v2 triggers; boilerplate skipped when the list name is given (is_boilerplate)",
        "cut_off": 0.5,
        "record_rule": "label if any flagged piece has P(yes) >= 0.5",
        "victim_rule": "no text-layer labels if Qwen3-4B P(victim) >= 0.5 (v4 role answers, unchanged)",
        "design_data": "old check pool, random 300 and held-out samples (used to design v5; not proof data)",
        "files_sha256_16": {p: sha(p) for p in ["scripts/checker.py", "scripts/highlighter.py",
                                                 "data/labelling_rules_pdg84.md", "data/blind_labelling_instructions.md",
                                                 "cache/fresh/checker_answers_v5.csv", "cache/checker_pyes_v5.csv"]},
    }
    json.dump(frozen, open(FROZEN, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps(frozen["v5"], indent=2))


if __name__ == "__main__":
    main()
