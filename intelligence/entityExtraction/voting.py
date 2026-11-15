"""Filter entity predictions and apply two-of-three voting."""

from collections import defaultdict
import re

from .contract import MODEL_KEYS


MODELS = MODEL_KEYS

GENERIC = {
    "commission",
    "bureau",
    "department",
    "office",
    "court",
    "agency",
    "government",
    "state",
    "secretariat",
    "authorities",
    "president",
    "secretary",
    "director",
    "judge",
    "attorney",
    "mayor",
    "senator",
    "minister",
    "ambassador",
    "member",
}

TITLE_PREFIX = re.compile(
    r"^(?:(?:Mr|Mrs|Ms|Dr|Prof|Atty|Hon|Engr)\.?|"
    r"Secretary|Senator|President|Director|Judge|"
    r"Mayor|Attorney|Dame|Sir)\s+",
    re.IGNORECASE,
)


def apply_filter(mentions, threshold=0.8):
    """Filter predictions and remove titles from person names."""

    filtered = []

    for original in mentions:
        mention = dict(original)

        if mention["entity_type"] == "INDIVIDUAL":
            while True:
                prefix = TITLE_PREFIX.match(mention["name"])

                if prefix is None:
                    break

                removed_length = prefix.end()

                mention["name"] = mention["name"][removed_length:]
                mention["start"] += removed_length
                mention["field_start"] += removed_length

        normalized_name = " ".join(
            mention["name"].lower().split()
        )

        if normalized_name.startswith("the "):
            normalized_name = normalized_name[4:]

        if (
            mention["name"]
            and mention["score"] >= threshold
            and normalized_name not in GENERIC
        ):
            filtered.append(mention)

    return filtered


def accept_votes(per_model, threshold=0.8):
    """Accept matching spans supported by at least two models."""

    if set(per_model) != set(MODELS):
        raise ValueError(
            "All three successful model outputs are required; "
            "failed models are not negative votes."
        )

    support = defaultdict(dict)

    for model, mentions in per_model.items():
        eligible_mentions = apply_filter(
            mentions,
            threshold=threshold,
        )

        for mention in eligible_mentions:
            key = (
                mention["start"],
                mention["end"],
                mention["entity_type"],
            )

            previous = support[key].get(model)

            # Each model contributes at most one vote per span/type.
            if (
                previous is None
                or mention["score"] > previous["score"]
            ):
                support[key][model] = mention

    accepted = []

    for by_model in support.values():
        if len(by_model) < 2:
            continue

        representative = max(
            by_model.values(),
            key=lambda mention: mention["score"],
        )

        accepted.append({
            **representative,
            "votes": len(by_model),
            "models": sorted(by_model),
            "source_scores": {
                model: mention["score"]
                for model, mention in by_model.items()
            },
        })

    return sorted(
        accepted,
        key=lambda mention: (
            mention["start"],
            mention["end"],
            mention["entity_type"],
        ),
    )