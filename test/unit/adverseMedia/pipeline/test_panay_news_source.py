"""Checks for the Panay News API configuration."""

from pathlib import Path

import yaml

from ingestion.apiCollector.collector import _iter_pages
from ingestion.apiCollector.models import ApiCollectorTask


def test_panay_requests_configured_categories_in_descending_pages():
    root = Path(__file__).resolve().parents[4]
    config = yaml.safe_load((root / "config/mediaSources.yaml").read_text(encoding="utf-8"))
    source = config["sources"]["PANAY_NEWS"]
    task = ApiCollectorTask.from_config(source)
    task.throttle_delay = 0

    class Transport:
        def __init__(self):
            self.queries = []

        def get_json(self, url, params):
            self.queries.append(dict(params))
            return [{"id": n} for n in range(20)] if params["page"] == 1 else []

    transport = Transport()
    assert len(list(_iter_pages(task, transport))) == 1
    assert task.transport == "browser"
    assert task.record_id_path == source["identity"]["external_id"]["field"] == "id"
    assert [query["page"] for query in transport.queries] == [1, 2]
    assert transport.queries[0]["per_page"] == 20
    assert transport.queries[0]["categories"] == "32,39,109,41,40,65"
    assert transport.queries[0]["orderby"] == "date"
    assert transport.queries[0]["order"] == "desc"
