"""Combine frozen lookup rules and CPU student predictions for a watchlist member."""

import re
from pathlib import Path

from intelligence.risk.result import calculation_version, implementation_version
from intelligence.risk.watchlistRiskCategory import highlighter, lookupRules, watchlistText
from intelligence.risk.watchlistRiskCategory.student import VICTIM_LABEL, WatchlistStudent


class WatchlistRiskClassifier:
    """Calculate a watchlist assessment without database access."""

    def __init__(self, model_path=None, threads=4):
        self.student = WatchlistStudent(model_path, threads)
        contract = self.student.contract
        self.labels = dict(zip(contract.artifact_labels, zip(contract.label_ids, contract.display_labels)))
        scope = set(self.student.manifest["text_labels"])
        self.triggers = highlighter.compile_triggers(
            {key: values for key, values in highlighter.TAXONOMY_TRIGGERS.items() if key in scope},
            {key: values for key, values in highlighter.EXTRA_TRIGGERS.items() if key in scope},
        )
        self.rules_version = implementation_version([lookupRules.__file__])
        self.runtime_version = implementation_version([
            __file__, highlighter.__file__, watchlistText.__file__,
            Path(__file__).parent / "watchlistRiskCategory/student.py",
            Path(__file__).parent / "labelContract.py", Path(__file__).parent / "result.py",
        ])
        self.calculation_version = calculation_version(
            self.student.artifact_version, self.student.policy_version,
            self.rules_version, self.runtime_version,
        )

    def classify(self, payload):
        """Return labels and passage decisions tied to the frozen model recipe."""
        sources = list(dict.fromkeys(
            item["ListName"].strip() for item in payload.get("Sources") or []
            if isinstance(item, dict) and isinstance(item.get("ListName"), str) and item["ListName"].strip()
        ))
        if len(sources) != 1:
            raise ValueError("A source member assessment requires exactly one distinct Sources[].ListName.")
        source = sources[0]
        text = watchlistText.render(payload, source)
        units = highlighter.split_units(text)
        candidates = highlighter.flag_units(units, self.triggers, source=source, version="v6")
        names, texts = [], []
        context = self.student.policy["context_chars"]
        for index, label, _ in candidates:
            before = units[index - 1][1][-context:] if index else ""
            after = units[index + 1][1][:context] if index + 1 < len(units) else ""
            names.append(label)
            texts.append(f"{units[index][0]}: {units[index][1]} || {before} [...] {after}")
        if candidates:
            names.append(VICTIM_LABEL)
            texts.append(re.sub(r"^Source list:.*\n", "", text)[:self.student.policy["victim_record_chars"]])
        scores = self.student.score(names, texts)
        victim = bool(candidates) and scores[-1] >= self.student.policy["victim_threshold"]
        categories = {}
        for name in lookupRules.record_rule_labels(source, payload):
            label_id, display_name = self.labels[name]
            categories[label_id] = {"riskCategoryId": label_id, "riskCategoryName": display_name}
        review_required = {}
        for (index, name, _), score in zip(candidates, scores):
            label_id, display_name = self.labels[name]
            selected = score >= self.student.policy["text_threshold"] and not victim
            if selected and label_id not in categories:
                item = review_required.setdefault(label_id, {
                    "riskCategoryId": label_id,
                    "riskCategoryName": display_name,
                    "method": "ml",
                    "score": float(score),
                    "scoreType": "student_sigmoid",
                    "threshold": self.student.policy["text_threshold"],
                    "evidence": [],
                })
                item["score"] = max(item["score"], float(score))
                item["evidence"].append({"field": units[index][0], "text": units[index][1]})
        return {
            "assessmentStatus": "CLASSIFIED",
            "riskCategories": [categories[key] for key in sorted(categories)],
            "reviewRequired": [review_required[key] for key in sorted(review_required)],
            "metadata": {
                "modelVersion": self.student.manifest["model_id"],
                "taxonomyVersion": self.student.contract.taxonomy_version,
                "calculationVersion": self.calculation_version,
            },
        }
