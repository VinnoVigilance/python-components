"""Precompute the search-support fields Elasticsearch would normally build at
index time.

Elasticsearch is deferred, so instead of relying on an analyzer chain at query
time we materialise the same artefacts into each canonical record: a
transliterated + folded name, a token list, and a phonetic key. Every function
here is a pure ``str -> str`` (or ``str -> list``) transform with no I/O, so the
post-normalization handlers can call them per Name/Alias and the whole thing
stays unit-testable in isolation.

The design choice that makes this tractable across scripts is *transliterate
first*: ``anyascii`` romanises any script (Arabic, Cyrillic, CJK, Greek...) to
ASCII, and every downstream step (fold, tokenize, phonetic) then runs on that
single romanised form. One code path covers every language, and the phonetic
key works for non-Latin names too.

Known limitation: transliteration is lossy. Unvocalised Arabic ("محمد" ->
"mhmd") drops the short vowels the Latin spelling ("Muhammad") keeps, so their
phonetic keys will not always collide. That is inherent to romanising an
abjad, not a defect here.
"""

import re
import unicodedata
from pathlib import Path

import pandas as pd
import pycountry
from anyascii import anyascii
from doublemetaphone import doublemetaphone


# Unicode-name prefix -> a coarse script/language label. Detection is by the
# dominant script of the *original* (pre-transliteration) text, because once a
# name is romanised every script looks Latin. These are scripts, not languages:
# a script maps to a language only where it is effectively 1:1 for the names we
# see (Arabic, Han, Hangul...); Latin and Cyrillic are left as the script name
# since one script covers many languages.
_SCRIPT_LABELS = {
    "LATIN": "Latin",
    "ARABIC": "Arabic",
    "CYRILLIC": "Cyrillic",
    "GREEK": "Greek",
    "HEBREW": "Hebrew",
    "CJK": "Chinese",
    "HANGUL": "Korean",
    "HIRAGANA": "Japanese",
    "KATAKANA": "Japanese",
    "DEVANAGARI": "Devanagari",
    "THAI": "Thai",
}


def to_english(text) -> str:
    """Romanise any script to ASCII. The single entry point every other
    transform builds on."""
    if text is None:
        return ""

    return anyascii(str(text)).strip()


def normalize_text(text) -> str:
    """Fold a name to a canonical comparison form: romanise, strip diacritics,
    lowercase, drop punctuation, collapse whitespace.

    Mirrors an Elasticsearch ``lowercase`` + ``asciifolding`` + ``trim``
    normalizer. Digits are kept (vessel names, ordinals); everything else that
    is not ``a-z0-9`` becomes a separator.
    """
    english = to_english(text)

    if not english:
        return ""

    # anyascii already returns ASCII, but a defensive NFKD + combining-mark
    # strip costs nothing and guards against any stray composed character.
    english = unicodedata.normalize("NFKD", english)
    english = "".join(c for c in english if not unicodedata.combining(c))

    english = english.lower()
    english = re.sub(r"[^a-z0-9]+", " ", english)

    return english.strip()


def tokenize(text) -> list:
    """Split the folded form into de-duplicated tokens, order preserved.

    Mirrors an Elasticsearch ``standard`` tokenizer feeding a ``unique`` filter,
    so partial-name search ("kakolele") can hit a full name ("Frank Kakolele
    Bwambale").
    """
    normalized = normalize_text(text)

    if not normalized:
        return []

    seen = set()
    tokens = []

    for token in normalized.split(" "):
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)

    return tokens


def phonetic_key(text) -> str:
    """Build a Double Metaphone key so "sounds-like" spellings collide
    (Mohammed / Muhammad / Mohamad).

    Computed per token and space-joined rather than over the whole string, so
    multi-word names stay aligned token-for-token. Uses the primary code, with
    the secondary code as a fallback when a token has no primary encoding.
    """
    tokens = tokenize(text)

    if not tokens:
        return ""

    codes = []

    for token in tokens:
        primary, secondary = doublemetaphone(token)
        code = primary or secondary

        if code:
            codes.append(code)

    return " ".join(codes)


def detect_language(text) -> str:
    """Return a coarse script label for the dominant script of the *original*
    text, or "" if it has no letters.

    This is script detection, not language detection: from characters alone you
    can tell Arabic from Cyrillic but not English from French. Callers should
    only use it to fill an empty Language, never to override a value the source
    provided.
    """
    if text is None:
        return ""

    counts = {}

    for ch in str(text):
        if not ch.isalpha():
            continue

        try:
            name = unicodedata.name(ch)
        except ValueError:
            continue

        prefix = name.split(" ")[0]
        label = _SCRIPT_LABELS.get(prefix)

        if label:
            counts[label] = counts.get(label, 0) + 1

    if not counts:
        return ""

    return max(counts, key=counts.get)


def normalize_number(number) -> str:
    """Reduce an identifier to bare alphanumerics, uppercased.

    Source IDs arrive with spacing, hyphens, dots, slashes and stray quotes
    ("A-1234 / 56", 'AB.12"34'); those separators are cosmetic and differ
    between lists for the same document, so they defeat exact matching. We keep
    only A-Z and 0-9 and uppercase, the same shape ICAO 9303 machine-readable
    travel documents use for their zone. anyascii first folds any full-width or
    non-Latin digits/letters to ASCII before stripping.
    """
    if number is None:
        return ""

    romanized = to_english(number)
    return re.sub(r"[^A-Za-z0-9]", "", romanized).upper()


# =========================================================
# COUNTRY NAME -> ISO2
# =========================================================

# The picklist is the authority on which ISO2 codes are allowed. We resolve a
# free-text country name to a code, then only accept it if it is in this set;
# an unresolved or out-of-set name yields "" rather than a guess.
_RULES_DIR = Path(__file__).resolve().parent.parent / "data" / "rules"
_PICKLIST_FILE = _RULES_DIR / "pickLists.xlsx"
_VALID_ISO2 = None

# Sanctions lists write country names in forms pycountry cannot resolve on its
# own: parenthetical/qualified spellings, historical names, and ambiguous short
# forms (Congo the country vs Congo the DRC). Keys are the *cleaned, uppercased*
# name (see _clean_country_name). Extend this as unresolved names surface.
_COUNTRY_ALIASES = {
    "DEMOCRATIC REPUBLIC OF THE CONGO": "CD",
    "CONGO, DEMOCRATIC REPUBLIC OF": "CD",
    "CONGO DEMOCRATIC REPUBLIC OF": "CD",
    "DR CONGO": "CD",
    "ZAIRE": "CD",
    "CONGO": "CG",
    "REPUBLIC OF THE CONGO": "CG",
    "IRAN": "IR",
    "SYRIA": "SY",
    "PALESTINE": "PS",
    "PALESTINIAN": "PS",
    "PALESTINIAN TERRITORY": "PS",
    "PALESTINIAN TERRITORY, OCCUPIED": "PS",
    "STATE OF PALESTINE": "PS",
    "NORTH KOREA": "KP",
    "SOUTH KOREA": "KR",
    # Historical: the union dissolved in 2006; Serbia is the successor state.
    "SERBIA AND MONTENEGRO": "RS",
}


def _load_valid_iso2() -> set:
    """The set of ISO2 codes the picklist permits (Countries.Country), cached."""
    global _VALID_ISO2

    if _VALID_ISO2 is None:
        df = pd.read_excel(_PICKLIST_FILE)
        mask = df["Path"].astype(str).str.strip() == "Countries.Country"

        _VALID_ISO2 = {
            str(v).strip().upper()
            for v in df.loc[mask, "Value"].dropna()
            if str(v).strip()
        }

    return _VALID_ISO2


def _clean_country_name(name: str) -> str:
    """Drop parentheticals and stray punctuation so both the alias lookup and
    pycountry see a tidy name: "Iran (Islamic Republic of)" -> "Iran"."""
    cleaned = re.sub(r"\(.*?\)", " ", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(",.").strip()
    return cleaned


def _pycountry_iso2(name: str):
    """Exact lookup first, then fuzzy; None if pycountry can't decide."""
    if not name:
        return None

    try:
        return pycountry.countries.lookup(name).alpha_2
    except LookupError:
        pass

    try:
        return pycountry.countries.search_fuzzy(name)[0].alpha_2
    except Exception:
        return None


def country_to_iso2(name, valid_codes=None) -> str:
    """Resolve a free-text country name to a picklist-valid ISO2 code, or "".

    Order: reject collapsed multi-country strings -> curated alias table ->
    pycountry (exact then fuzzy) -> validate against the picklist. Anything that
    does not land on an allowed code returns "" so we never emit a guessed or
    non-standard code.
    """
    if name is None:
        return ""

    raw = str(name).strip()

    if not raw:
        return ""

    # e.g. "['Central African Republic', 'Chad']" — two nationalities collapsed
    # into one string upstream. We can't pick one code; leave it for the mapping
    # fix that should split them into separate Countries[] entries.
    if raw.startswith("["):
        return ""

    if valid_codes is None:
        valid_codes = _load_valid_iso2()

    cleaned = _clean_country_name(raw)

    code = _COUNTRY_ALIASES.get(cleaned.upper())

    if code is None:
        code = _pycountry_iso2(raw) or _pycountry_iso2(cleaned)

    if code and code.upper() in valid_codes:
        return code.upper()

    return ""
