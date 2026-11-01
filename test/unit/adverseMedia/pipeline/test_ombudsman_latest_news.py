"""PDG-121: exercise the configured source through the real media pipeline."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import yaml

from ingestion.apiCollector.collector import _iter_pages
from ingestion.apiCollector.models import ApiCollectorTask
from services.adverseMediaPipeline.mediaNormalizationService import MediaNormalizationService
from services.adverseMediaPipeline.mediaRawRecordService import MediaRawRecordService

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[4]
DATASET = "OMBUDSMAN_PH_LATEST_NEWS"


def _config():
    config = yaml.safe_load((ROOT / "config/mediaSources.yaml").read_text(encoding="utf-8"))
    return config["global"], config["sources"][DATASET]


def test_source_normalizes_real_image_and_pdf_records():
    global_config, source = _config()
    fixture = ROOT / "test/fixtures/media/OMBUDSMAN_PH_LATEST_NEWS_extracted_sample.jsonl"
    records = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines()]
    with redirect_stdout(io.StringIO()):
        raw = MediaRawRecordService().process(source_config=source, records=records)
        results = MediaNormalizationService(
            global_config=global_config, source_config=source
        ).normalize_many(raw)
    by_id = {record["Sources"][0]["SourceRecordId"]: record for record in results}
    assert set(by_id) == {133877, 2154}
    for record in by_id.values():
        assert record["Sources"][0]["DatasetName"] == DATASET
        assert record["Sources"][0]["SourceName"] == "OMBUDSMAN_PH"
        assert record["Sources"][0]["SourceType"] == "Official"
        assert record["Publisher"]["Name"] == "Office of the Ombudsman (Philippines)"
        assert record["Content"]["Title"]
        assert record["Dates"][0]["Type"] == "Published"
        assert record["URLs"][0]["URLType"] == "Article"
    assert by_id[133877]["Attachments"] == [{
        "Type": "Image",
        "URL": "https://www.ombudsman.gov.ph/wp-content/uploads/2026/07/PRESS-STATEMENT-JULY-03-2026-216x300.jpg",
    }]
    assert by_id[2154]["Attachments"] == [{
        "Type": "Document",
        "URL": "https://www.ombudsman.gov.ph/wp-content/uploads/2024/12/LIST-OF-AWARDEES-FOR-THE-2024-YE-HIP-AWARDS.pdf",
    }]


def test_full_scan_requests_descending_pages_until_empty():
    _, source = _config()
    source = {**source, "api_config": {**source["api_config"], "throttle_delay": 0}}
    task = ApiCollectorTask.from_config(source)
    assert source["discovery"]["policy"] == "full_scan"

    class Transport:
        def __init__(self):
            self.queries = []

        def get_json(self, url, params):
            self.queries.append(dict(params))
            offset = params["offset"]
            return [{"id": n} for n in range(offset, min(offset + 100, 200))]

    transport = Transport()
    pages = list(_iter_pages(task, transport))
    assert [len(page) for page in pages] == [100, 100]
    assert [q["offset"] for q in transport.queries] == [0, 100, 200]
    for query in transport.queries:
        assert query["rest_route"] == "/wp/v2/posts"
        assert query["categories"] == 64
        assert query["per_page"] == 100
        assert query["orderby"] == "date"
        assert query["order"] == "desc"
        assert "page" not in query
