"""
Preprocessing tests for the CIA World Leaders list.

The list arrives as one record per country holding a nested list of leaders
(the same shape from all three sources: the live crawler and both historical
PDF eras). The list's preprocessing must fan that out into one record per
leader and give each a stable seat key, so this pins that wiring:

  * explosion         one country with N leaders -> N flat records
  * carry-down        each leader keeps its country / last_updated / note
  * seat key          external_id = <ListName>-<sha256(country|position)>,
                      so the SAME seat chains across editions under
                      `continuous` versioning even when the holder changes
  * vacant retention  a seat with no incumbent (name=None) is KEPT, so a
                      newly-empty seat supersedes its previous holder rather
                      than leaving the old holder wrongly current
"""

import pytest

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from transforms.preProcessingEngine import PreProcessingEngine

pytestmark = pytest.mark.unit

LIST_NAME = "CIA-WORLD-LEADERS-HISTORICAL"


@pytest.fixture()
def rules():
    return WATCHLIST_CONFIGS[LIST_NAME]["preprocessing"]


@pytest.fixture()
def raw_records():
    """Two countries in the extract shape; Bermuda carries a vacant seat."""
    return [
        {
            "detail": {
                "country": "Afghanistan",
                "last_updated": "20 Dec 2017",
                "note": None,
                "leaders": [
                    {"position": "Pres.", "name": "Ashraf GHANI"},
                    {"position": "Min. of Defense", "name": "Tariq Shah BAHRAMI"},
                ],
            }
        },
        {
            "detail": {
                "country": "Bermuda",
                "last_updated": "N/A",
                "note": "(British colony)",
                "leaders": [
                    {"position": "Governor", "name": "George FERGUSSON"},
                    {"position": "Deputy Governor", "name": None},
                ],
            }
        },
    ]


def _run(rules, raw_records):
    return PreProcessingEngine().preprocess(raw_records, rules)


def test_explodes_one_record_per_leader(rules, raw_records):
    out = _run(rules, raw_records)
    # 2 + 2 leaders across the two countries, vacant seat included.
    assert len(out) == 4


def test_carries_country_context_onto_each_leader(rules, raw_records):
    out = _run(rules, raw_records)
    ghani = next(r for r in out if r["name"] == "Ashraf GHANI")
    assert ghani["country"] == "Afghanistan"
    assert ghani["last_updated"] == "20 Dec 2017"

    governor = next(r for r in out if r["position"] == "Governor")
    assert governor["country"] == "Bermuda"
    assert governor["note"] == "(British colony)"


def test_external_id_is_prefixed_seat_key(rules, raw_records):
    out = _run(rules, raw_records)
    for record in out:
        assert record["external_id"].startswith(f"{LIST_NAME}-")
        digest = record["external_id"][len(f"{LIST_NAME}-"):]
        assert len(digest) == 64  # sha256 hex


def test_seat_key_is_country_plus_position_not_person(rules, raw_records):
    """The id keys on the seat, so a re-run with a different holder in the same
    seat produces the SAME external_id (that is what lets versioning chain it)."""
    out = _run(rules, raw_records)
    ghani = next(r for r in out if r["name"] == "Ashraf GHANI")

    successor = [
        {
            "detail": {
                "country": "Afghanistan",
                "last_updated": "1 Jan 2022",
                "note": None,
                "leaders": [{"position": "Pres.", "name": "Someone ELSE"}],
            }
        }
    ]
    successor_id = _run(rules, successor)[0]["external_id"]
    assert ghani["external_id"] == successor_id


def test_vacant_seat_is_retained_with_a_valid_id(rules, raw_records):
    out = _run(rules, raw_records)
    vacant = [r for r in out if r["name"] is None]
    assert len(vacant) == 1
    assert vacant[0]["position"] == "Deputy Governor"
    assert vacant[0]["external_id"].startswith(f"{LIST_NAME}-")
