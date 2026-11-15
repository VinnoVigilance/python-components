"""Rule-based name grouping within one article."""

import collections
import hashlib
import json
import re
import unicodedata

from .voting import apply_filter


WORD = re.compile(
    r"[^\W\d_]+(?:[-\u2019'][^\W\d_]+)*",
    re.UNICODE,
)

PUNCT = r"[\s,;:\u060c\u061b()\[\]'\"\u201c\u201d\u2018\u2019\xab\xbb\-\u2013\u2014]*"

ALIAS_LINK = re.compile(
    r"^"
    + PUNCT
    + r"(?:a\.?\s*k\.?\s*a\.?|alias|also known as|known as)"
    + PUNCT
    + r"$",
    re.IGNORECASE,
)

ORG_ABBR_LINK = re.compile(
    r"^\s*[,:]?\s*\(\s*[\"\u201c\u2018']?\s*$"
)

SUFFIXES = {
    "jr", "sr", "ii", "iii", "iv", "v",
}

TITLE_WORDS = {
    "mr", "mrs", "ms", "dr", "prof", "atty", "hon", "sir", "dame",
    "rep", "sen", "gov", "gen", "lt", "col", "capt", "supt",
    "secretary", "president", "director", "judge", "mayor", "senator",
    "minister", "ambassador", "subject", "subjects", "suspect", "suspects",
    "chief", "officer", "inspector", "superintendent", "police",
    "spokesperson", "oic", "commander", "councilor", "congressman",
    "representative",
}


def norm(name):
    """Normalize for comparison while preserving source spellings."""

    normalized = unicodedata.normalize("NFC", name)
    normalized = normalized.translate(
        str.maketrans({
            "\u2019": "'",
            "\u2018": "'",
        })
    )

    return " ".join(normalized.casefold().split())


def words(name):
    return [
        word.casefold()
        for word in WORD.findall(name)
    ]


def person_tokens(name):
    parts = words(name)

    while parts and parts[-1] in SUFFIXES:
        parts.pop()

    return parts


def latin(text):
    letters = [
        char
        for char in text
        if char.isalpha()
    ]

    return bool(letters) and all(
        "LATIN" in unicodedata.name(char, "")
        for char in letters
    )


def acronym_matches(full, short):
    if not re.fullmatch(r"[A-Z][A-Z0-9-]{1,13}", short):
        return False

    skip = {
        "the", "of", "and", "for", "on", "in", "at", "to",
    }

    initials = "".join(
        word[0]
        for word in WORD.findall(full)
        if word.casefold() not in skip
    ).upper()

    return short.replace("-", "") == initials


def json_safe_key(entity_type, names):
    return json.dumps(
        [entity_type, sorted(names)],
        ensure_ascii=False,
    )


def group_mentions(
    row,
    mentions,
    candidates=None,
    mode="conservative",
):
    """Return groups and unresolved links for one article."""

    if mode not in {"none", "explicit", "conservative"}:
        raise ValueError("Unknown grouping mode")

    by_name = collections.OrderedDict()

    for mention in sorted(
        mentions,
        key=lambda item: (
            item["start"],
            item["end"],
            item["entity_type"],
        ),
    ):
        key = (
            mention["entity_type"],
            mention["name"],
        )
        by_name.setdefault(key, []).append(mention)

    keys = list(by_name)

    index = {
        key: position
        for position, key in enumerate(keys)
    }

    parent = list(range(len(keys)))

    links = []
    unresolved = []
    preferred = []

    def find(position):
        while parent[position] != position:
            parent[position] = parent[parent[position]]
            position = parent[position]

        return position

    def join(left, right, rule, evidence):
        left_root = find(left)
        right_root = find(right)

        if left_root == right_root:
            return

        parent[right_root] = left_root

        is_source = (
            evidence in row["title"]
            or evidence in row["body"]
        )

        quote = (
            evidence
            if is_source
            else by_name[keys[left]][0]["evidence_sentence"]
        )

        links.append({
            "left": keys[left][1],
            "right": keys[right][1],
            "entity_type": keys[left][0],
            "rule": rule,
            "evidence": quote,
            "explanation": "" if is_source else evidence,
        })

    if mode != "none":
        # Rule 1: case, spacing and equivalent apostrophes.
        seen = {}

        for position, (entity_type, name) in enumerate(keys):
            key = (
                entity_type,
                norm(name),
            )

            if key in seen:
                join(
                    seen[key],
                    position,
                    "CASE_OR_SPACING_VARIANT",
                    "Source spellings preserved in mentions.",
                )
            else:
                seen[key] = position

        # Rules 2 and 3: explicit aliases and matching acronyms.
        ordered = sorted(
            mentions,
            key=lambda item: (
                item["field"],
                item["start"],
                item["end"],
            ),
        )

        for left, right in zip(ordered, ordered[1:]):
            if (
                left["field"] != right["field"]
                or left["entity_type"] != right["entity_type"]
                or left["end"] > right["start"]
            ):
                continue

            gap = row["text"][
                left["end"]:right["start"]
            ]

            left_id = index[
                (left["entity_type"], left["name"])
            ]

            right_id = index[
                (right["entity_type"], right["name"])
            ]

            if ALIAS_LINK.fullmatch(gap):
                context = row["text"][
                    max(0, left["start"] - 25):right["end"] + 25
                ]

                real_pattern = re.compile(
                    r"\b(?:real|genuine)\s+"
                    + re.escape(right["name"]),
                    re.IGNORECASE,
                )

                if real_pattern.search(row["text"]):
                    unresolved.append({
                        "name": right["name"],
                        "reason": (
                            "ALIAS_ALSO_MARKED_AS_REAL_OR_GENUINE_PERSON"
                        ),
                        "evidence": context,
                    })
                    continue

                join(
                    left_id,
                    right_id,
                    "EXPLICIT_ALIAS_LINK",
                    row["text"][left["start"]:right["end"]],
                )

                preferred.append(left_id)

            elif (
                left["entity_type"] == "ORGANIZATION"
                and ORG_ABBR_LINK.fullmatch(gap)
                and acronym_matches(
                    left["name"],
                    right["name"],
                )
            ):
                tail = row["text"][
                    right["end"]:right["end"] + 8
                ]

                if re.match(
                    r"^\s*[\"\u201d\u2019']?\s*\)",
                    tail,
                ):
                    quote = (
                        row["text"][left["start"]:right["end"]]
                        + tail[:tail.index(")") + 1]
                    )

                    join(
                        left_id,
                        right_id,
                        "EXPLICIT_MATCHING_ACRONYM",
                        quote,
                    )

                    preferred.append(left_id)

    if mode == "conservative":
        # Rule 4: Latin surnames, after checking for rivals.
        raw = apply_filter(
            candidates or mentions,
            threshold=0.2,
        )

        for position, (entity_type, short) in enumerate(keys):
            tokens = person_tokens(short)

            if (
                entity_type != "INDIVIDUAL"
                or len(tokens) != 1
                or not latin(short)
                or len(tokens[0]) < 2
            ):
                continue

            surname = tokens[0]
            owners = {}

            for other, (other_type, full) in enumerate(keys):
                full_tokens = person_tokens(full)

                if (
                    other_type == "INDIVIDUAL"
                    and latin(full)
                    and len(full_tokens) >= 2
                    and full_tokens[-1] == surname
                ):
                    owners.setdefault(
                        find(other),
                        [],
                    ).append(other)

            if not owners or find(position) in owners:
                continue

            full_names = [
                keys[other][1]
                for members in owners.values()
                for other in members
            ]

            if len(owners) != 1:
                unresolved.append({
                    "name": short,
                    "reason": "SHARED_SURNAME",
                    "candidates": full_names,
                })
                continue

            owner_nodes = next(iter(owners.values()))

            owner_forms = {
                norm(keys[other][1])
                for other in owner_nodes
            }

            owner_mentions = [
                mention
                for other in owner_nodes
                for mention in by_name[keys[other]]
            ]

            conflicts = []

            for candidate in raw:
                candidate_tokens = person_tokens(
                    candidate["name"]
                )

                if (
                    candidate["entity_type"] != "INDIVIDUAL"
                    or not latin(candidate["name"])
                    or len(candidate_tokens) < 2
                    or candidate_tokens[-1] != surname
                ):
                    continue

                same_form = (
                    norm(candidate["name"]) in owner_forms
                )

                same_occurrence = any(
                    candidate["start"] <= mention["start"]
                    and candidate["end"] == mention["end"]
                    and norm(candidate["name"]).endswith(
                        norm(mention["name"])
                    )
                    for mention in owner_mentions
                )

                if not same_form and not same_occurrence:
                    conflicts.append(candidate["name"])

            # Inspect source text for a possible unextracted rival.
            allowed_words = {
                word
                for name in full_names
                for word in person_tokens(name)
            }

            given_pattern = re.compile(
                r"(?<!\w)([^\W\d_][\w'\u2019-]+)\s+((?i:"
                + re.escape(short)
                + r"))(?!\w)",
                re.UNICODE,
            )

            for hit in given_pattern.finditer(row["text"]):
                given = hit.group(1).casefold()

                if (
                    hit.group(1)[0].isupper()
                    and latin(hit.group(1))
                    and given not in allowed_words
                    and given not in TITLE_WORDS
                ):
                    conflicts.append(hit.group(0))

            if conflicts:
                unresolved.append({
                    "name": short,
                    "reason": (
                        "OTHER_POSSIBLE_PERSON_WITH_SURNAME"
                    ),
                    "candidates": sorted(
                        set(full_names + conflicts)
                    ),
                })
                continue

            join(
                owner_nodes[0],
                position,
                "UNIQUE_LATIN_SURNAME_IN_ARTICLE",
                (
                    "No competing full person name in model "
                    "candidates or simple source-name guard."
                ),
            )

    buckets = collections.defaultdict(list)

    for position in range(len(keys)):
        buckets[find(position)].append(position)

    result = []

    for members in buckets.values():
        member_keys = [
            keys[position]
            for position in members
        ]

        names = [
            key[1]
            for key in member_keys
        ]

        entity_type = member_keys[0][0]

        values = sorted(
            [
                mention
                for key in member_keys
                for mention in by_name[key]
            ],
            key=lambda item: (
                item["start"],
                item["end"],
            ),
        )

        full_members = [
            position
            for position in members
            if len(person_tokens(keys[position][1])) >= 2
        ]

        priority = [
            position
            for position in preferred
            if position in members
            and position in full_members
        ]

        if priority:
            canonical = keys[priority[0]][1]
        else:
            chosen = max(
                members,
                key=lambda position: (
                    len(person_tokens(keys[position][1])) >= 2,
                    len(person_tokens(keys[position][1])),
                    len(keys[position][1]),
                    -position,
                ),
            )

            canonical = keys[chosen][1]

        evidence = next(
            mention["evidence_sentence"]
            for mention in values
            if mention["name"] == canonical
        )

        digest = hashlib.sha256(
            json_safe_key(
                entity_type,
                names,
            ).encode("utf-8")
        ).hexdigest()[:10]

        group_links = [
            link
            for link in links
            if link["entity_type"] == entity_type
            and link["left"] in names
            and link["right"] in names
        ]

        result.append({
            "local_entity_id": (
                f'{row["sample_id"]}:{entity_type[0]}:{digest}'
            ),
            "entity_type": entity_type,
            "name": canonical,
            "evidence_sentence": evidence,
            "aliases": [
                name
                for name in names
                if name != canonical
            ],
            "mentions": values,
            "grouping_evidence": group_links,
        })

    result.sort(
        key=lambda group: (
            group["mentions"][0]["start"],
            group["entity_type"],
        )
    )

    before = {
        (
            item["entity_type"],
            item["name"],
            item["start"],
            item["end"],
        )
        for item in mentions
    }

    after = {
        (
            item["entity_type"],
            item["name"],
            item["start"],
            item["end"],
        )
        for group in result
        for item in group["mentions"]
    }

    if before != after:
        raise AssertionError(
            "Grouping changed accepted mentions"
        )

    return result, unresolved