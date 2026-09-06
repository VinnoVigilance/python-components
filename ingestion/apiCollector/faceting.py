"""Adaptive fan-out planner for list APIs that cap results but report a total.

Slices the dataset into queries each at or under the cap, subdividing only the
slices still over it. Pure: the caller injects ``get_total(params) -> int``.

Facets (ordered split rules) declared in config:
  enum       fixed values (``values`` or ``values_ref``); ``disjoint`` values
             partition (early-stop), else overlap (dedup handles it).
  range      bisect a numeric range (``min_param``/``max_param``).
  substring  probe a "contains" filter a character at a time, up to ``max_depth``.
``complete: False`` hands records with no value to the next facet as a catch-all.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


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


DEFAULT_SUBSTRING_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass
class FanoutPlan:
    """Result of planning: ``leaves`` to fetch, ``unresolved`` slices still over
    cap, and the ``root_total`` the API reported."""

    leaves: List[Dict[str, Any]] = field(default_factory=list)
    unresolved: List[Dict[str, Any]] = field(default_factory=list)
    root_total: int = 0


def plan_fanout(
    get_total: Callable[[Dict[str, Any]], int],
    base_params: Dict[str, Any],
    cap: int,
    facets: List[Dict[str, Any]],
) -> FanoutPlan:
    """Plan the fan-out and report any slice the facets could not get under cap."""

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
    """Recursively split ``params`` until each slice is at or under ``cap``."""

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

            if facet.get("disjoint", True):
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
                for value in _resolve_values(facet):
                    child = {**params, param: value}

                    _collect(
                        get_total, child, cap, get_total(child),
                        facets, index + 1, plan, base_params,
                    )

            if not facet.get("complete", True):
                _collect(
                    get_total, dict(params), cap, total,
                    facets, index + 1, plan, base_params,
                )

            return

        if facet_type == "range":
            first_open = facet["max_param"] not in params

            children = _range_children(facet, params)

            if children is None:
                index += 1
                continue

            for child in children:
                _collect(
                    get_total, child, cap, get_total(child),
                    facets, index, plan, base_params,
                )

            if first_open and not facet.get("complete", True):
                _collect(
                    get_total, dict(params), cap, total,
                    facets, index + 1, plan, base_params,
                )

            return

        if facet_type == "substring":
            children = _substring_children(facet, params)

            if children is None:
                index += 1
                continue

            for child in children:
                _collect(
                    get_total, child, cap, get_total(child),
                    facets, index, plan, base_params,
                )

            if not facet.get("complete", True):
                _collect(
                    get_total, dict(params), cap, total,
                    facets, index + 1, plan, base_params,
                )

            return

        index += 1

    plan.leaves.append(dict(params))
    plan.unresolved.append({"params": dict(params), "total": total})


def _range_children(
    facet: Dict[str, Any],
    params: Dict[str, Any],
) -> Optional[List[Dict[str, Any]]]:
    """One bisection step; None once the range is a single point."""

    min_param = facet["min_param"]
    max_param = facet["max_param"]

    if max_param not in params:
        return [{**params, min_param: facet["low"], max_param: facet["high"]}]

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
    """One deepening step (append a letter from the alphabet); None at max_depth."""

    param = facet["param"]
    alphabet = facet.get("alphabet", DEFAULT_SUBSTRING_ALPHABET)
    max_depth = facet.get("max_depth", 3)

    prefix = params.get(param, "")

    if len(prefix) >= max_depth:
        return None

    return [{**params, param: prefix + char} for char in alphabet]


def _resolve_values(facet: Dict[str, Any]) -> List[Any]:
    """An enum facet's values: inline ``values`` or a ``values_ref`` built-in."""

    if "values" in facet:
        return facet["values"]

    return BUILTIN_VALUE_SETS.get(facet.get("values_ref"), [])
