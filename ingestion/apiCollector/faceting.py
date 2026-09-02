"""
Adaptive fan-out planner for list APIs that cap results but report a total.

Some list endpoints return at most N records per query yet still report the
true ``total`` for any filter. To pull the whole dataset you must slice it into
queries each at or under the cap. This planner does that *adaptively*: it asks
"how many?" for a slice and only subdivides the slices still over the cap, so a
small slice costs one query and a huge one is split just enough.

Everything is data-driven -- the cap and the facets (which query params to
split on, and their values/ranges) are passed in, not hardcoded -- so it works
for any capped API and any source declares its own facets in config:

    facets = [
        {"type": "enum",  "param": "sexId", "values": ["M", "F", "U"]},
        {"type": "range", "min_param": "ageMin", "max_param": "ageMax",
                          "low": 0, "high": 120},
        {"type": "enum",  "param": "nationality", "values_ref": "country_codes"},
    ]

It is pure logic: the caller injects ``get_total(params) -> int`` -- the only
I/O -- so it is unit-testable without a network call.

Facet order matters and is the caller's choice. Two guidelines that drove the
Red Notices config: put a *complete* facet first (one whose values cover every
record, e.g. sex incl. an "unknown" bucket) so no record is dropped; and give a
deep fallback facet last for the rare slice that a single sex + single age year
still leaves over the cap. If every facet is exhausted and a slice is still over
the cap, it is emitted anyway (capped) and the caller's completeness check
surfaces the shortfall.

Facet types:
  * ``enum``      split on a fixed value list (``values`` or ``values_ref``).
                  ``disjoint: True`` (default) partitions -- each record has one
                  value, so probing stops once the counts reach the slice total.
                  ``disjoint: False`` overlaps -- probe every value, no
                  early-stop; dedup drops records seen under two values.
                  ``complete: True`` (default) means the values cover every
                  record. Set ``complete: False`` when some records have no
                  value for this field (e.g. a notice with no nationality):
                  after probing, the slice is also handed to the next facet so a
                  downstream catch-all reaches the records that matched no value
                  -- otherwise they are silently dropped.
  * ``range``     bisect a numeric range (``min_param``/``max_param``) until it
                  is a single point.
  * ``substring`` a bottomless last resort for a slice no bounded facet can
                  shrink: probe a *contains* filter (e.g. a name) one character
                  at a time, deepening ("A" -> "AA".."AZ") only where a child is
                  still over cap, up to ``max_depth``. Values overlap, so it
                  relies on dedup; because it can always grow another character,
                  its granularity has no fixed ceiling as the data grows.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional


# ISO 3166-1 alpha-2 codes, referenced from config by ``"values_ref":
# "country_codes"`` so a source need not inline ~250 codes. Only used as a deep
# fallback split, so an unused/invalid code simply resolves to a total of 0.
COUNTRY_CODES = (
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI "
    "BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN "
    "CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK "
    "FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM "
    "HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN "
    "KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK "
    "ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP "
    "NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW "
    "SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF "
    "TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI "
    "VN VU WF WS YE YT ZA ZM ZW"
).split()


BUILTIN_VALUE_SETS = {
    "country_codes": COUNTRY_CODES,
}


# Default alphabet for a ``substring`` facet -- the characters an Interpol
# forename/name can hold (transliterated Latin, upper-cased by the API's match).
# A source can override it via the facet's ``alphabet`` key.
DEFAULT_SUBSTRING_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass
class FanoutPlan:
    """
    The result of planning a fan-out.

    ``leaves``     the slices to fetch (each meant to be <= cap).
    ``unresolved`` slices that are STILL over the cap after every facet was
                   tried -- the signal that the facets/cap no longer cover the
                   data and a new facet is needed. Each is {"params", "total"}.
    ``root_total`` the whole-dataset total the API reported.
    """

    leaves: List[Dict[str, Any]] = field(default_factory=list)
    unresolved: List[Dict[str, Any]] = field(default_factory=list)
    root_total: int = 0


def plan_fanout(
    get_total: Callable[[Dict[str, Any]], int],
    base_params: Dict[str, Any],
    cap: int,
    facets: List[Dict[str, Any]],
) -> FanoutPlan:
    """
    Plan the fan-out and report any slice the facets could not get under the cap.

    ``base_params`` are the source's static params; each leaf adds the facet
    filters for one slice.
    """

    plan = FanoutPlan(root_total=get_total(base_params))

    _collect(
        get_total,
        dict(base_params),
        cap,
        plan.root_total,
        facets,
        facet_index=0,
        plan=plan,
        base_params=dict(base_params),
    )

    return plan


def iter_leaf_queries(
    get_total: Callable[[Dict[str, Any]], int],
    base_params: Dict[str, Any],
    cap: int,
    facets: List[Dict[str, Any]],
) -> Iterator[Dict[str, Any]]:
    """Convenience wrapper: yield just the leaf slices."""

    yield from plan_fanout(get_total, base_params, cap, facets).leaves


def _collect(
    get_total: Callable[[Dict[str, Any]], int],
    params: Dict[str, Any],
    cap: int,
    total: int,
    facets: List[Dict[str, Any]],
    facet_index: int,
    plan: FanoutPlan,
    base_params: Dict[str, Any],
) -> None:
    """
    Recursively split ``params`` until each slice is at or under ``cap``,
    recording leaves (and any slice no facet could shrink) into ``plan``.
    """

    if total <= 0:
        return

    if total <= cap:
        plan.leaves.append(dict(params))
        return

    index = facet_index

    while index < len(facets):
        facet = facets[index]
        facet_type = facet.get("type")

        if facet_type == "enum":
            param = facet["param"]

            # A facet is "disjoint" when each record has exactly one value for it
            # (e.g. sex) -- the values partition the slice, so once the counts we
            # have probed add up to the slice total, every remaining value must be
            # empty and we can stop early. A facet where a record can hold several
            # values (nationality -- dual nationals; arrest-warrant country --
            # wanted by several countries) is "disjoint: False": its value-slices
            # OVERLAP, so the early-stop (which assumes the counts sum to the slice
            # total) would stop early and drop records. There we probe every value;
            # the union still covers everyone and the collector's dedup drops the
            # records that appear under two values.
            if facet.get("disjoint", True):
                # Each value selects a disjoint partition, so once the counts we
                # have probed add up to ``total`` every remaining value must be
                # empty -- stop probing the zero tail.
                remaining = total

                for value in _resolve_values(facet):
                    child = {**params, param: value}
                    child_total = get_total(child)

                    _collect(
                        get_total, child, cap, child_total,
                        facets, index + 1, plan, base_params,
                    )

                    remaining -= child_total

                    if remaining <= 0:
                        break
            else:
                # Overlapping facet: probe every value, no early-stop.
                for value in _resolve_values(facet):
                    child = {**params, param: value}

                    _collect(
                        get_total, child, cap, get_total(child),
                        facets, index + 1, plan, base_params,
                    )

            # An *incomplete* facet -- one whose values do not cover every
            # record (e.g. some Interpol notices carry no nationality at all) --
            # leaves a remainder that matched NONE of the values, so it sits in
            # no child slice. Left as-is that remainder is silently dropped
            # (never fetched, not even flagged unresolved). Hand the whole slice
            # to the NEXT facet so a downstream catch-all -- e.g. the name
            # substring facet, which every record can match -- still reaches
            # those records; the collector's dedup drops the ones already
            # fetched under a value. Facets are ``complete`` by default (e.g.
            # sex, whose "unknown" bucket covers everyone), so they skip this
            # and pay nothing.
            if not facet.get("complete", True):
                _collect(
                    get_total, dict(params), cap, total,
                    facets, index + 1, plan, base_params,
                )

            return

        if facet_type == "range":
            # ``max_param not in params`` => the range has not been opened for
            # this slice yet, i.e. this is the top of the range for it.
            first_open = facet["max_param"] not in params

            children = _range_children(facet, params)

            if children is None:
                # Range exhausted at a single point -- try the next facet.
                index += 1
                continue

            for child in children:
                # Stay on this facet so the range keeps bisecting.
                _collect(
                    get_total, child, cap, get_total(child),
                    facets, index, plan, base_params,
                )

            # A record with NO value for this range (e.g. a notice with no date
            # of birth, so no age) matches none of the sub-ranges and sits in no
            # child -- the range's version of an incomplete facet. When the range
            # is first opened on a slice, also hand the whole (range-less) slice
            # to the NEXT facet so those no-value records reach a downstream
            # catch-all (nationality/name); dedup drops the ones already fetched.
            # Fires once per slice, only when complete=False.
            if first_open and not facet.get("complete", True):
                _collect(
                    get_total, dict(params), cap, total,
                    facets, index + 1, plan, base_params,
                )

            return

        if facet_type == "substring":
            # A last-resort split for a slice a bounded facet (sex/age/
            # nationality/warrant) can no longer shrink. The API's name filter
            # is a *contains* match, so value "A" selects every record whose
            # ``param`` contains "A" -- single-letter PRESENCE, which is
            # position-independent (so suffix-safe) and, unioned over the whole
            # alphabet, covers every record with a non-empty ``param``. Values
            # overlap heavily, so we probe every one with no early-stop and let
            # dedup drop the repeats. With ``max_depth: 1`` the facet stays at
            # single letters and hands an over-cap letter-cell to the NEXT facet
            # -- e.g. forename-letter then surname-letter forms a 26x26 grid.
            # A larger ``max_depth`` is a rare within-field backstop: it appends
            # a character to shrink a single field that the grid alone cannot.
            children = _substring_children(facet, params)

            if children is None:
                # Max depth reached -- hand the slice to the next facet.
                index += 1
                continue

            for child in children:
                # Stay on this facet so the substring keeps deepening.
                _collect(
                    get_total, child, cap, get_total(child),
                    facets, index, plan, base_params,
                )

            # A record with no value for this field (e.g. no forename -- only a
            # surname) matches none of the letter-cells and sits in no child.
            # As with the enum/range facets, an incomplete substring facet hands
            # the slice to the NEXT facet (the next name field) so those records
            # are still reached; the final field flags any leftover unresolved.
            if not facet.get("complete", True):
                _collect(
                    get_total, dict(params), cap, total,
                    facets, index + 1, plan, base_params,
                )

            return

        # Unknown facet type -- skip it.
        index += 1

    # No facet could split this slice further and it is still over the cap.
    # Fetch what we can (capped) but flag it so a new facet can be added.
    plan.leaves.append(dict(params))
    plan.unresolved.append({"params": dict(params), "total": total})


def _range_children(
    facet: Dict[str, Any],
    params: Dict[str, Any],
) -> Optional[List[Dict[str, Any]]]:
    """
    One bisection step for a numeric range facet.

    Opens the full range first, then halves it each call. Returns None once the
    range is a single point (nothing left to split on this facet).
    """

    min_param = facet["min_param"]
    max_param = facet["max_param"]

    if max_param not in params:
        return [
            {**params, min_param: facet["low"], max_param: facet["high"]}
        ]

    low = params[min_param]
    high = params[max_param]

    if low >= high:
        return None

    mid = (low + high) // 2

    return [
        {**params, min_param: low, max_param: mid},
        {**params, min_param: mid + 1, max_param: high},
    ]


def _substring_children(
    facet: Dict[str, Any],
    params: Dict[str, Any],
) -> Optional[List[Dict[str, Any]]]:
    """
    One deepening step for a substring facet.

    At ``max_depth: 1`` (the intended default here) the values are the single
    letters "A".."Z": each is a *presence* probe (``param`` contains that
    letter), which is position-independent -- so it is suffix-safe, and unioned
    over the alphabet it covers every record with a non-empty ``param``. An
    over-cap letter-cell is then handed to the NEXT facet (the next name field),
    so forename-letter x surname-letter forms a 26x26 grid with no appending.

    A larger ``max_depth`` is a rare within-field backstop for a single field
    that alone cannot shrink: it appends a character ("A" -> "AA".."AZ"). Each
    longer value is a subset of the shorter one (a name containing "AB" also
    contains "A"), so the recursion narrows monotonically. (Appending is not
    position-independent, so keep depth shallow and lean on the cross-field grid
    for coverage.) Returns None once the probe reaches ``max_depth`` characters.
    """

    param = facet["param"]
    alphabet = facet.get("alphabet", DEFAULT_SUBSTRING_ALPHABET)
    max_depth = facet.get("max_depth", 3)

    prefix = params.get(param, "")

    if len(prefix) >= max_depth:
        return None

    return [{**params, param: prefix + char} for char in alphabet]


def _resolve_values(facet: Dict[str, Any]) -> List[Any]:
    """
    Values for an enum facet: an inline ``values`` list, or a ``values_ref``
    naming a built-in set (e.g. "country_codes").
    """

    if "values" in facet:
        return facet["values"]

    return BUILTIN_VALUE_SETS.get(facet.get("values_ref"), [])
