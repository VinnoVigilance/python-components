"""
LLM layer (Layer 3) for the Risk Category Engine.

Reads the *narrative prose* of a mapped record (mainly ``Comments[].text``) and
proposes subcategory / indicator labels that the deterministic rule layer cannot
see, then stacks them on top of the rule labels via the same append-then-dedup
merge used by ``ruleMatcher``.

Design (decided with the data in front of us):

  * Runs on every *included* record, not just sanction-only ones. It only ever
    *adds* labels; it never overwrites what the rules found. If the prose says
    nothing classifiable, it adds nothing - that is a valid, desirable outcome.

  * The prompt is built from three parts:
        prose      - the record's informative narrative (junk filtered out)
        frame      - one context line: list Nature + sanctions Program + entity type
        vocabulary - the Categories/SubCategories/Indicators with their descriptions
    Plus the hard rules: quote evidence for every label, output must match schema.

  * Three layers of output validation so nothing invalid can escape:
        1. generation  - Ollama is given a vocabulary-enum JSON schema
        2. vocabulary  - every (Category, SubCategory) / Indicator is re-checked
                         against the loaded config; unknown labels are dropped
        3. grounding   - the evidence quote must actually occur in the prose

  * Confidence gate: >= ASSERT_THRESHOLD -> asserted; TENTATIVE..ASSERT ->
    kept but flagged review=True; below TENTATIVE -> dropped. Thresholds are
    module constants for now (easy to lift into the Excel config later).

The prompt template lives in ``data/prompts/risk_classification.txt`` (loaded at
runtime, same convention as transforms/llmExtractor.py); the vocabulary is loaded
from the Excel config. Neither requires a code change to edit.

The model itself is reached through ``intelligence/llm.py`` (Ollama), which talks
to ``localhost:11434`` - i.e. the real runs below must execute ON the box where
Ollama lives.

Usage
-----
    # Dry run - build and print the exact prompt + schema, DO NOT call the model:
    python -m risk.llmClassifier --dry-run

    # Classify ONE real source: data/final/<SRC>_final.jsonl -> data/risk/<SRC>_classified.jsonl
    python -m risk.llmClassifier --source OFAC-SDN

    # Classify every source found in data/final/:
    python -m risk.llmClassifier --all
"""

import argparse
import hashlib
import json
import re
import time
from copy import deepcopy
from pathlib import Path

from risk.configLoader import RiskConfig, load_risk_config, _norm
from risk.ruleMatcher import RuleMatcher, load_jsonl_safe, _merge_contributions

# --- paths (mirrors transforms/llmExtractor.py convention) --------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT_PATH = ROOT_DIR / "data" / "prompts" / "risk_classification.txt"
FINAL_DIR = ROOT_DIR / "data" / "final"        # mapper output (input to us)
RISK_DIR = ROOT_DIR / "data" / "risk"          # our classified output

# --- confidence gate (kept simple; can move into the Excel config later) ------
ASSERT_THRESHOLD = 0.75     # >= this  -> asserted
TENTATIVE_THRESHOLD = 0.50  # >= this and < assert -> kept, flagged review

# The model self-reports 1.0 on everything, so we do NOT ask it for a number.
# Instead it picks a categorical strength, which we map to a score. Each level
# maps to a distinct outcome: explicit -> asserted, implied -> review, weak -> dropped.
STRENGTH_TO_CONF = {"explicit": 0.9, "implied": 0.7, "weak": 0.45}

# Subcategories the LLM must NOT emit: the rule layer always assigns "Sanctioned",
# so offering it to the model just wastes generation time (it re-added it on ~23%
# of records) and adds noise. We hide it from the schema + vocabulary entirely.
LLM_EXCLUDE_SUBCATS = {"Sanctioned"}

DEFAULT_MODEL = "qwen2.5:14b-instruct"


# ---------------------------------------------------------------------------
# 1. Prose extraction + junk filter
# ---------------------------------------------------------------------------

# Comment fragments that carry no crime signal - only legal citations / links.
# Feeding these invites the model to invent a label, so we strip them.
_JUNK_PATTERNS = [
    re.compile(r"^\s*section\s+\d", re.I),
    re.compile(r"executive\s+order", re.I),
    re.compile(r"unsc\s+resolution", re.I),
    re.compile(r"^\s*(un )?security council resolution", re.I),
    re.compile(r"^\s*resolution\s+\d", re.I),
    re.compile(r"interpol[- ]un .*web link", re.I),
    re.compile(r"^\s*https?://\S+\s*$", re.I),
]


def _astext(t):
    if isinstance(t, list):
        return " ".join(_astext(x) for x in t)
    return str(t) if t is not None else ""


def _strip_urls(text: str) -> str:
    return re.sub(r"https?://\S+", "", text).strip()


def _is_junk(text: str) -> bool:
    stripped = _strip_urls(text)
    if len(stripped) < 15 or len(stripped.split()) < 5:
        # Too short to be a narrative worth a model call ("HAMAS - Ramallah",
        # "Subject to Secondary Sanctions", "Corrigendum 2021/..."). These
        # produced nothing on the first runs but cost 30-70s each.
        return True
    return any(p.search(stripped) for p in _JUNK_PATTERNS)


def informative_prose(record) -> str:
    """Join the record's comment text, dropping pure citations / links.

    Returns "" when nothing informative remains - the caller then skips the
    record entirely (no model call), which is most of OFAC and all of UKSL.
    """
    parts = []
    for c in record.get("Comments") or []:
        text = _astext(c.get("text")).strip()
        if not text or _is_junk(text):
            continue
        parts.append(_strip_urls(text))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


# ---------------------------------------------------------------------------
# 2. Frame line (soft context: Nature + Program/regime + entity type)
# ---------------------------------------------------------------------------

def _programs(record) -> list:
    out = []
    for p in record.get("Programs") or []:
        prog = _norm(p.get("Program"))
        if prog:
            out.append(prog)
    return out


def build_frame(record, source: str, cfg: RiskConfig) -> str:
    entry = cfg.list_scope.get(source)
    nature = entry.nature if entry else None
    programs = _programs(record)
    etype = _norm(record.get("EntityType"))

    bits = []
    if nature:
        bits.append(f"source list: {nature}")
    if programs:
        bits.append(f"sanctions program/regime: {', '.join(sorted(set(programs))[:4])}")
    if etype:
        bits.append(f"entity type: {etype}")
    return "; ".join(bits) if bits else "source list: (unknown)"


# ---------------------------------------------------------------------------
# 3. Vocabulary rendering + JSON schema
# ---------------------------------------------------------------------------

def render_vocabulary(cfg: RiskConfig) -> str:
    """Human-readable Category > SubCategory > Indicator list with descriptions,
    exactly the label space the model is allowed to choose from."""
    tree = cfg.vocabulary_tree()
    lines = []
    for cat, cv in tree.items():
        subs = {s: v for s, v in cv["subcategories"].items()
                if s not in LLM_EXCLUDE_SUBCATS}
        if not subs:
            continue  # e.g. "Sanctions" has only the excluded "Sanctioned"
        desc = f" - {cv['description']}" if cv.get("description") else ""
        lines.append(f"* {cat}{desc}")
        for sub, sv in subs.items():
            sdesc = f" - {sv['description']}" if sv.get("description") else ""
            lines.append(f"    - {sub}{sdesc}")
            for ind, idesc in sv.get("indicators", {}).items():
                itail = f" - {idesc}" if idesc else ""
                lines.append(f"        . {ind}{itail}")
    return "\n".join(lines)


def build_schema(cfg: RiskConfig) -> dict:
    """JSON schema for Ollama structured output. Fields are enums drawn from the
    loaded vocabulary, so the model is constrained to real labels at generation.
    Excluded subcategories (e.g. Sanctioned) are omitted so the model cannot emit
    them - saving generation time and noise."""
    subs = {s: m for s, m in cfg.subcategories.items()
            if s not in LLM_EXCLUDE_SUBCATS}
    live_cats = {m["parent"] for m in subs.values()}
    categories = sorted(c for c in cfg.categories if c in live_cats)
    subcategories = sorted(subs.keys())
    indicators = sorted(cfg.indicators.keys())

    return {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": categories},
                        "subcategory": {"type": "string", "enum": subcategories},
                        "indicator": {"type": "string", "enum": indicators},
                        "strength": {"type": "string", "enum": ["explicit", "implied", "weak"]},
                        "evidence": {"type": "string"},
                    },
                    "required": ["category", "subcategory", "strength", "evidence"],
                },
            }
        },
        "required": ["labels"],
    }


# ---------------------------------------------------------------------------
# 4. Prompt assembly (template lives in data/prompts/risk_classification.txt)
# ---------------------------------------------------------------------------

# The instruction text is kept OUT of the code, in a prompt file, following the
# same convention as transforms/llmExtractor.py. Edit the wording there without
# touching Python. Placeholders filled at runtime:
#     {{vocabulary}} - the Categories/SubCategories/Indicators rendered from Excel
#     {{context}}    - the per-record frame line (Nature + Program + entity type)
#     {{narrative}}  - the record's informative prose
_PROMPT_CACHE = {}


def load_prompt_template(path=None) -> str:
    """Read the prompt template file (cached). Defaults to
    data/prompts/risk_classification.txt."""
    path = str(path or DEFAULT_PROMPT_PATH)
    if path not in _PROMPT_CACHE:
        _PROMPT_CACHE[path] = Path(path).read_text(encoding="utf-8")
    return _PROMPT_CACHE[path]


def assemble_prompt(record, source: str, cfg: RiskConfig, prose: str,
                    template: str = None, vocabulary: str = None) -> str:
    # The fixed block (instructions + vocabulary) sits FIRST in the template and is
    # byte-identical across every record, so Ollama/llama.cpp reuses its KV cache
    # (prompt-prefix caching) instead of re-reading ~600 vocab tokens each call.
    # Only the short, varying tail (context + narrative) is recomputed. This is a
    # large speedup on CPU - see the ~37s -> ~3s drop on repeated prompts.
    template = template if template is not None else load_prompt_template()
    vocab = vocabulary if vocabulary is not None else render_vocabulary(cfg)
    frame = build_frame(record, source, cfg)
    return (
        template
        .replace("{{vocabulary}}", vocab)
        .replace("{{context}}", frame)
        .replace("{{narrative}}", prose)
    )


# ---------------------------------------------------------------------------
# 5. Output validation (layers 2 + 3) and confidence gate
# ---------------------------------------------------------------------------

def _ground_norm(s: str) -> str:
    """Normalize for the grounding test: lowercase, unify curly quotes/dashes,
    drop punctuation, collapse whitespace. Punctuation is stripped because the
    model often adds commas to a space-separated list ("Kidnapping, Murder" vs
    "Kidnapping Murder"), which otherwise fails an exact match."""
    s = s.lower()
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'),
                 ("”", '"'), ("–", "-"), ("—", "-")):
        s = s.replace(a, b)
    s = re.sub(r"[^\w\s]", " ", s)          # drop punctuation
    return re.sub(r"\s+", " ", s).strip()


def _is_grounded(evidence: str, prose_norm: str) -> bool:
    """True if the model's quote is really in the narrative. Accepts an exact
    (normalized) substring, or - to recover quotes where the model stitched two
    real phrases together - the first 6 words appearing contiguously."""
    ev = _ground_norm(evidence)
    if not ev:
        return False
    if ev in prose_norm:
        return True
    words = ev.split()
    return len(words) >= 6 and " ".join(words[:6]) in prose_norm


def validate_and_gate(raw_labels, prose: str, cfg: RiskConfig):
    """Return (accepted_contributions, rejected) after vocab + grounding checks
    and the confidence gate. ``rejected`` explains every dropped label."""
    allowed_pairs = cfg.allowed_labels()          # {(category, subcategory)}
    indicator_parent = {i: m["parent"] for i, m in cfg.indicators.items()}
    prose_l = _ground_norm(prose)

    accepted, rejected = [], []
    for lab in raw_labels or []:
        cat = _norm(lab.get("category"))
        sub = _norm(lab.get("subcategory"))
        ind = _norm(lab.get("indicator"))
        strength = (_norm(lab.get("strength")) or "").lower()
        evidence = _norm(lab.get("evidence")) or ""

        # Layer 2: vocabulary check
        if sub in LLM_EXCLUDE_SUBCATS:
            # rules own these (e.g. Sanctioned); ignore if the model emits one anyway
            rejected.append((lab, f"{sub} is assigned by rules, not the LLM"))
            continue
        if (cat, sub) not in allowed_pairs:
            rejected.append((lab, f"unknown label {cat}/{sub}"))
            continue
        if ind and indicator_parent.get(ind) != sub:
            # indicator that doesn't belong under this subcategory -> drop indicator only
            ind = None

        # Layer 3: grounding check
        if not _is_grounded(evidence, prose_l):
            rejected.append((lab, "evidence not found in narrative"))
            continue

        # Strength -> confidence, then gate. weak maps below the floor -> dropped.
        conf = STRENGTH_TO_CONF.get(strength)
        if conf is None:
            rejected.append((lab, f"invalid strength ({strength!r})"))
            continue
        if conf < TENTATIVE_THRESHOLD:
            rejected.append((lab, f"strength '{strength}' too weak"))
            continue

        accepted.append({
            "category": cat,
            "subcategory": sub,
            "indicator": ind,
            "confidence": conf,
            "strength": strength,
            "method": "llm",
            "evidence": f"{evidence[:120]} (llm)",
            "source": source_of(evidence),  # placeholder, overwritten by caller
            "review": conf < ASSERT_THRESHOLD,
        })
    return accepted, rejected


def source_of(_evidence):  # small helper kept explicit; real source set by caller
    return None


# ---------------------------------------------------------------------------
# 6. Orchestration
# ---------------------------------------------------------------------------

class LlmClassifier:
    def __init__(self, cfg: RiskConfig, model: str = DEFAULT_MODEL,
                 prompt_path=None):
        self.cfg = cfg
        self.model = model
        self.schema = build_schema(cfg)
        # Loaded once: the prompt template + the rendered vocabulary are constant
        # across every record, so the model's prompt prefix stays cacheable.
        self.template = load_prompt_template(prompt_path)
        self.vocabulary = render_vocabulary(cfg)
        self._cache = {}  # text-hash -> raw model labels (consistency + speed)

    def _call_model(self, prompt: str, prose: str):
        import hashlib
        key = hashlib.md5(prose.encode("utf-8")).hexdigest()
        if key in self._cache:
            return self._cache[key]
        from intelligence.llm import generate  # imported lazily: only needed for a real call
        out = generate(prompt, self.model, schema=self.schema)
        try:
            parsed = json.loads(out).get("labels", [])
        except json.JSONDecodeError:
            parsed = []
        self._cache[key] = parsed
        return parsed

    def enrich_record(self, classified_record, source: str, dry_run=False):
        """Take a record ALREADY run through the rule matcher and add LLM labels.
        In dry_run mode, returns the assembled prompt instead of calling the model.
        """
        record = deepcopy(classified_record)
        prose = informative_prose(record)
        if not prose:
            return {"prompt": None, "skipped": "no informative prose"} if dry_run else record

        prompt = assemble_prompt(record, source, self.cfg, prose,
                                 template=self.template, vocabulary=self.vocabulary)
        if dry_run:
            return {"prompt": prompt, "prose": prose}

        raw = self._call_model(prompt, prose)
        accepted, rejected = validate_and_gate(raw, prose, self.cfg)
        for a in accepted:
            a["source"] = source

        existing = [
            {
                "category": e["Category"], "subcategory": e["SubCategory"],
                "confidence": e["Confidence"], "method": e["Method"],
                "evidence": e["Evidence"][0] if e["Evidence"] else "",
                "source": e["Sources"][0] if e["Sources"] else source,
            }
            for e in record.get("RiskCategories", [])
        ]
        record["RiskCategories"] = _merge_contributions(existing + accepted)
        record["_llm_rejected"] = rejected
        return record


# ---------------------------------------------------------------------------
# 7. Production entry point - HOW TO INSERT YOUR DATA
# ---------------------------------------------------------------------------

def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def classify_source(source, model=DEFAULT_MODEL, in_dir=FINAL_DIR, out_dir=RISK_DIR,
                    cfg=None, checkpoint_every=200, verbose=True):
    """Classify ONE source end to end and write the enriched records.

    This is the production entry point. Data flows:

        data/final/<source>_final.jsonl        (the mapper's output = INPUT)
              |
              |  Layer 1+2  RuleMatcher    -> base label + rule labels
              |  Layer 3    LlmClassifier  -> subcategory/indicator from prose
              v
        data/risk/<source>_classified.jsonl    (same records + "RiskCategories")

    To onboard a NEW list later: drop its ``<name>_final.jsonl`` into data/final
    and call ``classify_source("<name>")`` - nothing else changes, because the
    vocabulary and prompt are loaded at runtime from the Excel and the prompt file.

    Identical prose is sent to the model only once (text-hash cache); progress is
    flushed to disk every ``checkpoint_every`` records.
    """
    cfg = cfg or load_risk_config()
    rules = RuleMatcher(cfg)
    clf = LlmClassifier(cfg, model=model)

    in_path = Path(in_dir) / f"{source}_final.jsonl"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{source}_classified.jsonl"

    records, skipped = load_jsonl_safe(in_path)
    included = cfg.is_included(source)
    results = []
    t0 = time.time()
    for i, raw in enumerate(records, 1):
        ruled = rules.classify_record(raw, list_name=source)
        # Excluded lists (e.g. DNFBP) carry only their (empty) rule result - no
        # LLM spend. Included lists get the prose enriched on top.
        rec = clf.enrich_record(ruled, source) if included else ruled
        rec.pop("_llm_rejected", None)
        results.append(rec)
        if verbose and i % 50 == 0:
            print(f"  {source}: {i}/{len(records)}  ({time.time() - t0:.0f}s)")
        if i % checkpoint_every == 0:
            _write_jsonl(out_path, results)
    _write_jsonl(out_path, results)
    if verbose:
        note = f", skipped {skipped} bad lines" if skipped else ""
        print(f"[{source}] {len(results)} records -> {out_path} "
              f"({time.time() - t0:.0f}s{note})")
    return out_path


def classify_all(model=DEFAULT_MODEL, **kw):
    """Classify every ``*_final.jsonl`` in data/final that has a config entry."""
    import glob
    import os
    cfg = load_risk_config()
    for path in sorted(glob.glob(str(FINAL_DIR / "*_final.jsonl"))):
        source = os.path.basename(path).replace("_final.jsonl", "")
        if source not in cfg.list_scope:
            print(f"[skip] {source}: no ListScope entry in the config")
            continue
        classify_source(source, model=model, cfg=cfg, **kw)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_SAMPLE_SOURCES = [
    ("OFAC-SDN", 3), ("DFAT", 4), ("UN-SANCTIONS", 3),
    ("EU-FINANCIAL-SANCTIONS", 2), ("ATC-DESIGNATED-TERRORIST-INDIVIDUALS", 3),
]


def _stratified_sample(cfg, limit_per):
    matcher = RuleMatcher(cfg)
    out = []
    for src, n in _SAMPLE_SOURCES:
        path = f"data/final/{src}_final.jsonl"
        try:
            recs, _ = load_jsonl_safe(path)
        except FileNotFoundError:
            continue
        classified = matcher.run(recs, list_name=src)
        picked = 0
        for cl in classified:
            if informative_prose(cl):
                out.append((src, cl))
                picked += 1
                if picked >= min(n, limit_per):
                    break
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Risk Category Engine - LLM layer (Layer 3).")
    ap.add_argument("--source", help="classify ONE real source end to end: "
                    "data/final/<SOURCE>_final.jsonl -> data/risk/")
    ap.add_argument("--all", action="store_true",
                    help="classify every source in data/final/")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the assembled prompt + schema for a few sample "
                         "records WITHOUT calling the model (safe anywhere)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, default=3, help="[dry-run] records per source")
    args = ap.parse_args()

    # Production: classify real data (needs Ollama on this machine).
    if args.source:
        classify_source(args.source, model=args.model)
        return
    if args.all:
        classify_all(model=args.model)
        return

    # Default: dry-run - build the exact prompt from the file + Excel, no model.
    cfg = load_risk_config(strict=False)
    clf = LlmClassifier(cfg, model=args.model)
    sample = _stratified_sample(cfg, args.limit)

    print("=" * 90)
    print("JSON SCHEMA handed to the model (enums truncated for readability):")
    sch = deepcopy(clf.schema)
    item = sch["properties"]["labels"]["items"]["properties"]
    for k in ("category", "subcategory", "indicator"):
        enum = item[k]["enum"]
        item[k]["enum"] = enum[:4] + [f"...(+{len(enum) - 4} more)"]
    print(json.dumps(sch, indent=2, ensure_ascii=False))

    for i, (src, rec) in enumerate(sample[:4], 1):
        res = clf.enrich_record(rec, src, dry_run=True)
        print("\n" + "=" * 90)
        print(f"SAMPLE {i}  [{src}]  EntityType={rec.get('EntityType')}")
        print(f"Rule labels already present: "
              f"{[(e['Category'], e['SubCategory']) for e in rec['RiskCategories']]}")
        if res.get("prompt") is None:
            print("(skipped:", res.get("skipped"), ")")
            continue
        print("-" * 90)
        print(res["prompt"])


if __name__ == "__main__":
    main()
