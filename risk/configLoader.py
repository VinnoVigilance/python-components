"""
Loader for the Risk Category Engine configuration (data/rules/riskClassification.xlsx).

Reads the five configuration sheets, cleans them (copy-paste from docs/tables leaves
non-breaking spaces and stray whitespace behind), checks that every cross-reference
resolves, and exposes the vocabulary in a shape both the rule matcher and the LLM
classifier can consume.

The vocabulary is loaded at runtime, so adding a row in the Excel and re-running the
pipeline is enough for the engine to consider it - no code change. This is what keeps
the engine general across lists and future-proof for new categories.

Sheets
------
ListScope      : which lists are classified, and the base label each one gets
Categories     : top-level risk categories + descriptions
SubCategories  : subcategories, each linked to a parent Category
Indicators     : fine-grained indicators, each linked to a parent SubCategory
Rules          : field-match rules that add labels on top of the ListScope base
"""

import math
import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

DEFAULT_CONFIG_PATH = "data/rules/riskClassification.xlsx"

# The value used in AppliesToList to mean "every list".
_ALL_LISTS_TOKEN = "(all)"


def _norm(value) -> Optional[str]:
    """Normalize a cell to a clean string, or None when empty.

    Collapses non-breaking spaces (\\xa0) and zero-width spaces into normal
    whitespace so that a value typed as "Organized\\xa0Crime" on one sheet
    matches "Organized Crime" typed on another.
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None

    text = str(value).replace("\xa0", " ").replace("​", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _norm_float(value) -> Optional[float]:
    text = _norm(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


@dataclass
class ListScopeEntry:
    list_name: str
    included: bool
    nature: Optional[str]
    default_category: Optional[str]
    default_subcategory: Optional[str]
    confidence: Optional[float]
    # Every base label the list carries, as (category, subcategory, confidence)
    # tuples, primary first. Most lists have exactly one. A few are inherently
    # more than one thing -- e.g. ATC is both a terrorism designation (Crime)
    # and a targeted financial sanction (Sanctions) -- and get one ListScope
    # ROW per nature; those rows accumulate here (see _parse_list_scope).
    # Auto-seeded from the primary so an entry built directly (e.g. in tests or
    # a single-row list) still exposes its one label.
    base_labels: list = field(default_factory=list)

    def __post_init__(self):
        if not self.base_labels and self.default_category:
            self.base_labels = [
                (self.default_category, self.default_subcategory, self.confidence)
            ]


@dataclass
class Rule:
    rule_id: str
    priority: float
    applies_to_list: Optional[str]  # None means "all lists"
    match_field: str
    match_type: str  # exact | contains | regex
    match_value: str
    add_category: Optional[str]
    add_subcategory: Optional[str]
    confidence: Optional[float]

    def applies_to(self, list_name: str) -> bool:
        return self.applies_to_list is None or self.applies_to_list == list_name


@dataclass
class RiskConfig:
    """The full, validated risk-classification vocabulary and rules."""

    list_scope: dict = field(default_factory=dict)          # list_name -> ListScopeEntry
    categories: dict = field(default_factory=dict)          # name -> description
    subcategories: dict = field(default_factory=dict)       # name -> {parent, description}
    indicators: dict = field(default_factory=dict)          # name -> {parent, description}
    rules: list = field(default_factory=list)               # list[Rule], priority-ordered

    # ---- convenience accessors --------------------------------------------

    def is_included(self, list_name: str) -> bool:
        entry = self.list_scope.get(list_name)
        return bool(entry and entry.included)

    def included_lists(self) -> list:
        return [name for name, e in self.list_scope.items() if e.included]

    def default_label(self, list_name: str):
        """Return (category, subcategory, confidence) for a list's base label,
        or None when the list is excluded / has no default."""
        entry = self.list_scope.get(list_name)
        if not entry or not entry.included or not entry.default_category:
            return None
        return (entry.default_category, entry.default_subcategory, entry.confidence)

    def default_labels(self, list_name: str) -> list:
        """Return EVERY base label for a list as (category, subcategory,
        confidence) tuples, or [] when the list is excluded / has no default.

        Single-category lists return one tuple; inherently dual-natured lists
        (e.g. ATC -> Crime/Terrorism + Sanctions/Sanctioned) return several.
        Callers that only need the primary use :meth:`default_label`.
        """
        entry = self.list_scope.get(list_name)
        if not entry or not entry.included:
            return []
        return list(entry.base_labels)

    def rules_for(self, list_name: str) -> list:
        return [r for r in self.rules if r.applies_to(list_name)]

    def allowed_labels(self) -> set:
        """The set of valid (category, subcategory) pairs - used to validate
        whatever the LLM returns, so it can never invent a label."""
        pairs = set()
        for sub, meta in self.subcategories.items():
            pairs.add((meta["parent"], sub))
        return pairs

    def vocabulary_tree(self) -> dict:
        """Nested category -> subcategory -> indicators view, with descriptions.

        This is the structure the LLM schema builder consumes to produce a
        vocabulary-locked, dynamically-generated enum.
        """
        tree = {}
        for cat, desc in self.categories.items():
            tree[cat] = {"description": desc, "subcategories": {}}

        for sub, meta in self.subcategories.items():
            parent = meta["parent"]
            tree.setdefault(parent, {"description": None, "subcategories": {}})
            tree[parent]["subcategories"][sub] = {
                "description": meta["description"],
                "indicators": {},
            }

        for ind, meta in self.indicators.items():
            parent = meta["parent"]
            for cat in tree.values():
                if parent in cat["subcategories"]:
                    cat["subcategories"][parent]["indicators"][ind] = meta["description"]
                    break

        return tree


# ---------------------------------------------------------------------------
# Sheet parsers
# ---------------------------------------------------------------------------

def _parse_list_scope(df: pd.DataFrame) -> dict:
    """Build ListName -> ListScopeEntry.

    A list normally occupies a single row. A list that is inherently more than
    one risk category (e.g. ATC = a terrorism designation that is also a
    targeted financial sanction) is written as SEVERAL rows sharing one
    ListName, one per nature; the extra rows accumulate as additional base
    labels on the same entry. This keeps every value in its own cell -- no
    delimited multi-value cells -- and stays fully backward-compatible with the
    one-row-per-list config.
    """
    out = {}
    for _, row in df.iterrows():
        name = _norm(row.get("ListName"))
        if not name:
            continue

        included = (_norm(row.get("Included")) or "").lower() == "yes"
        category = _norm(row.get("DefaultCategory"))
        subcategory = _norm(row.get("DefaultSubCategory"))
        confidence = _norm_float(row.get("Confidence"))

        entry = out.get(name)
        if entry is None:
            # First row for this list -> __post_init__ seeds base_labels from
            # the primary category (if any).
            out[name] = ListScopeEntry(
                list_name=name,
                included=included,
                nature=_norm(row.get("Nature")),
                default_category=category,
                default_subcategory=subcategory,
                confidence=confidence,
            )
            continue

        # Additional row for a list already seen -> another base label.
        entry.included = entry.included or included
        if category:
            if entry.default_category is None:
                entry.default_category = category
                entry.default_subcategory = subcategory
                entry.confidence = confidence
            label = (category, subcategory, confidence)
            if label not in entry.base_labels:
                entry.base_labels.append(label)
    return out


def _parse_categories(df: pd.DataFrame) -> dict:
    out = {}
    for _, row in df.iterrows():
        name = _norm(row.get("Category"))
        if not name:
            continue
        out[name] = _norm(row.get("Description"))
    return out


def _parse_subcategories(df: pd.DataFrame) -> dict:
    out = {}
    for _, row in df.iterrows():
        name = _norm(row.get("SubCategory"))
        if not name:
            continue
        out[name] = {
            "parent": _norm(row.get("ParentCategory")),
            "description": _norm(row.get("Description")),
        }
    return out


def _parse_indicators(df: pd.DataFrame) -> dict:
    out = {}
    for _, row in df.iterrows():
        name = _norm(row.get("Indicator"))
        if not name:
            continue
        out[name] = {
            "parent": _norm(row.get("ParentSubCategory")),
            "description": _norm(row.get("Description")),
        }
    return out


def _parse_rules(df: pd.DataFrame) -> list:
    rules = []
    for _, row in df.iterrows():
        rule_id = _norm(row.get("RuleID"))
        match_field = _norm(row.get("MatchField"))
        # Skip the blank filler rows that Excel keeps around.
        if not rule_id or not match_field:
            continue

        applies = _norm(row.get("AppliesToList"))
        if applies is None or applies.lower() == _ALL_LISTS_TOKEN:
            applies = None

        match_type = (_norm(row.get("MatchType")) or "exact").lower()

        rules.append(
            Rule(
                rule_id=rule_id,
                priority=_norm_float(row.get("Priority")) or 0.0,
                applies_to_list=applies,
                match_field=match_field,
                match_type=match_type,
                match_value=_norm(row.get("MatchValue")),
                add_category=_norm(row.get("AddCategory")),
                add_subcategory=_norm(row.get("AddSubCategory")),
                confidence=_norm_float(row.get("Confidence")),
            )
        )

    rules.sort(key=lambda r: r.priority)
    return rules


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate(config: RiskConfig) -> list:
    """Return a list of human-readable integrity problems (empty == clean)."""
    problems = []
    cats = set(config.categories)
    subs = set(config.subcategories)

    for sub, meta in config.subcategories.items():
        if meta["parent"] not in cats:
            problems.append(
                f"SubCategory '{sub}' points at ParentCategory "
                f"'{meta['parent']}' which is not in Categories."
            )

    for ind, meta in config.indicators.items():
        if meta["parent"] not in subs:
            problems.append(
                f"Indicator '{ind}' points at ParentSubCategory "
                f"'{meta['parent']}' which is not in SubCategories."
            )

    for name, entry in config.list_scope.items():
        if not entry.included:
            continue
        for category, subcategory, _conf in entry.base_labels:
            if category and category not in cats:
                problems.append(
                    f"ListScope '{name}' DefaultCategory "
                    f"'{category}' is not in Categories."
                )
            if subcategory and subcategory not in subs:
                problems.append(
                    f"ListScope '{name}' DefaultSubCategory "
                    f"'{subcategory}' is not in SubCategories."
                )

    for rule in config.rules:
        if rule.add_category and rule.add_category not in cats:
            problems.append(
                f"Rule '{rule.rule_id}' AddCategory "
                f"'{rule.add_category}' is not in Categories."
            )
        if rule.add_subcategory and rule.add_subcategory not in subs:
            problems.append(
                f"Rule '{rule.rule_id}' AddSubCategory "
                f"'{rule.add_subcategory}' is not in SubCategories."
            )
        if rule.match_type not in {"exact", "contains", "regex"}:
            problems.append(
                f"Rule '{rule.rule_id}' has unknown MatchType "
                f"'{rule.match_type}' (expected exact/contains/regex)."
            )

    return problems


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load_risk_config(path: str = DEFAULT_CONFIG_PATH, strict: bool = True) -> RiskConfig:
    """Load and validate the risk-classification workbook.

    Parameters
    ----------
    path : str
        Path to riskClassification.xlsx.
    strict : bool
        When True, raise ValueError if any cross-reference fails to resolve.
        When False, integrity problems are attached to the returned config's
        ``.problems`` attribute for inspection instead of raising.
    """
    sheets = pd.read_excel(path, sheet_name=None)

    required = {"ListScope", "Categories", "SubCategories", "Indicators", "Rules"}
    missing = required - set(sheets)
    if missing:
        raise ValueError(
            f"{path} is missing required sheet(s): {', '.join(sorted(missing))}"
        )

    config = RiskConfig(
        list_scope=_parse_list_scope(sheets["ListScope"]),
        categories=_parse_categories(sheets["Categories"]),
        subcategories=_parse_subcategories(sheets["SubCategories"]),
        indicators=_parse_indicators(sheets["Indicators"]),
        rules=_parse_rules(sheets["Rules"]),
    )

    problems = _validate(config)
    if problems and strict:
        raise ValueError(
            "Risk configuration has integrity problems:\n  - "
            + "\n  - ".join(problems)
        )
    config.problems = problems  # type: ignore[attr-defined]

    return config


if __name__ == "__main__":
    # Quick self-check: load the config and print a summary so you can verify
    # the Excel is well-formed before wiring the engine to it.
    cfg = load_risk_config(strict=False)

    print(f"Lists          : {len(cfg.list_scope)} "
          f"({len(cfg.included_lists())} included, "
          f"{len(cfg.list_scope) - len(cfg.included_lists())} excluded)")
    print(f"Categories     : {len(cfg.categories)}")
    print(f"SubCategories  : {len(cfg.subcategories)}")
    print(f"Indicators     : {len(cfg.indicators)}")
    print(f"Rules          : {len(cfg.rules)}")

    print("\nExcluded lists :", [n for n, e in cfg.list_scope.items() if not e.included])

    print("\nBase label per included list:")
    for name in cfg.included_lists():
        cat, sub, conf = cfg.default_label(name)
        print(f"  {name:40} -> {cat} / {sub}  (conf {conf})")

    print("\nRules (priority order):")
    for r in cfg.rules:
        scope = r.applies_to_list or "all"
        print(f"  [{r.priority:>4}] {r.rule_id}: {r.match_field} {r.match_type} "
              f"'{r.match_value}' -> {r.add_category}/{r.add_subcategory} "
              f"(conf {r.confidence}, lists: {scope})")

    problems = getattr(cfg, "problems", [])
    if problems:
        print(f"\n[!] {len(problems)} integrity problem(s):")
        for p in problems:
            print("   -", p)
    else:
        print("\n[OK] No integrity problems - every reference resolves.")
