"""
Risk Category Calculation Engine - single-member entry point.

The file-based entry points in ``risk/llmClassifier.py`` (``classify_source`` /
``classify_all``) read ``data/final/<src>_final.jsonl`` and write
``data/risk/<src>_classified.jsonl``. The database ETL described in the Data
Insertion Guideline ("Member Risk Category Calculation & Versioning") needs the
same calculation applied to ONE ``core.watchlist_member.full_payload`` pulled
from Postgres, returning the ``risk_details`` document that gets versioned into
``core.member_risk_category``.

This module is that bridge. It wraps the deterministic rule layers (always) and,
optionally, the LLM layer, and exposes ``RiskEngine.classify(full_payload)`` ->
``risk_details`` (a plain, JSON-serializable dict).

Design notes
------------
* The engine treats classification as a black box, exactly as the guideline
  intends: given a member's canonical payload, produce its risk labels. WHAT the
  labels are is decided by ``risk/ruleMatcher.py`` (+ ``risk/llmClassifier.py``);
  this module only adapts the call to a single DB record and shapes the output.

* ``risk_details`` is an OBJECT (matching the ``jsonb DEFAULT '{}'`` column), not
  a bare array, so it stays extensible (reviewer info, roll-ups, ... can be added
  later without changing the column contract). Today it holds one key:

      {"RiskCategories": [ {Category, SubCategory, Indicators,
                            Confidence, Method, Evidence, Sources}, ... ]}

  An empty ``RiskCategories`` list is a valid, truthful result (an excluded list
  such as DNFBP, or an included record that could not be classified).

* The LLM layer is OFF by default. It requires Ollama running on the host and is
  the expensive part of the pipeline; the deterministic layers alone are fully
  runnable and testable anywhere. Turn it on with ``RiskEngine(use_llm=True)``
  when executing on the model server.
"""

from typing import Any

from risk.configLoader import RiskConfig, _norm, load_risk_config
from risk.ruleMatcher import RuleMatcher


def _primary_list_name(full_payload: dict) -> str | None:
    """The list identity to hand the classifier as authoritative context.

    The rule matcher can derive list names from ``Sources[].ListName`` on its
    own, but passing the primary one explicitly mirrors how the file pipeline
    supplies ``list_name`` and gives the LLM frame a Nature to look up.
    """
    for item in full_payload.get("Sources") or []:
        if isinstance(item, dict):
            name = _norm(item.get("ListName"))
            if name:
                return name
    return None


class RiskEngine:
    """Applies the Risk Category Calculation Engine to a single member payload.

    Parameters
    ----------
    cfg:
        A loaded :class:`~risk.configLoader.RiskConfig`. Loaded from the Excel
        config if omitted, so the vocabulary/rules stay editable without a code
        change.
    use_llm:
        When True, the LLM layer (Layer 3) is stacked on top of the rule labels
        for every *included* record. Requires Ollama on the host.
    model:
        Ollama model name, only used when ``use_llm`` is True.
    """

    def __init__(
        self,
        cfg: RiskConfig | None = None,
        use_llm: bool = False,
        model: str | None = None,
    ) -> None:
        self.cfg = cfg or load_risk_config()
        self.rules = RuleMatcher(self.cfg)
        self.use_llm = use_llm

        self._llm = None
        if use_llm:
            # Imported lazily so the deterministic path never pulls in the LLM
            # stack (or its Ollama dependency) unless it is actually wanted.
            from risk.llmClassifier import DEFAULT_MODEL, LlmClassifier

            self._llm = LlmClassifier(self.cfg, model=model or DEFAULT_MODEL)

    def classify(self, full_payload: dict[str, Any]) -> dict[str, Any]:
        """Return the ``risk_details`` document for one member payload."""
        list_name = _primary_list_name(full_payload)

        ruled = self.rules.classify_record(full_payload, list_name=list_name)

        if self._llm is not None and self.cfg.is_included(list_name or ""):
            enriched = self._llm.enrich_record(ruled, list_name or "")
            enriched.pop("_llm_rejected", None)
            risk_categories = enriched.get("RiskCategories", [])
        else:
            risk_categories = ruled.get("RiskCategories", [])

        return {"RiskCategories": risk_categories}
