import json

from utils.jsonlRecordBuffer import JsonlRecordBuffer
from ingestion.crawler.crawler import _media_spool_paths
from ingestion.crawler.models import CrawlerTask


def test_jsonl_buffer_survives_reopen_without_loading_records(tmp_path):
    path = tmp_path / "records.jsonl"
    buffer = JsonlRecordBuffer(path, reset=True)
    buffer.append({"record_key": "a", "extracted": {"Title": "One"}})
    buffer.append({"record_key": "b", "extracted": {"Title": "خبر دوم"}})
    buffer.close()

    resumed = JsonlRecordBuffer(path)
    assert len(resumed) == 2
    assert [record["record_key"] for record in resumed] == ["a", "b"]
    assert resumed[1]["extracted"]["Title"] == "خبر دوم"
    resumed.close()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_complete_spool_manifest_contains_reusable_result(tmp_path):
    path = tmp_path / "records.pending.jsonl"
    manifest = tmp_path / "records.complete.json"
    buffer = JsonlRecordBuffer(path, reset=True)
    buffer.append({"record_key": "a"})
    buffer.close()
    manifest.write_text(json.dumps({
        "version": 1,
        "record_count": 1,
        "result": {"record_count": 1, "discovered_count": 1},
    }), encoding="utf-8")

    resumed = JsonlRecordBuffer(path)
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    assert metadata["record_count"] == len(resumed) == 1
    assert metadata["result"]["discovered_count"] == 1
    resumed.close()


def test_initial_incremental_and_retry_spools_cannot_overlap(tmp_path):
    def paths(scope):
        return _media_spool_paths(CrawlerTask(
            url="https://example.test", source_name="SRC", list_name="NEWS",
            download_dir=str(tmp_path), spool_scope=scope,
        ))

    initial = paths("initial")
    incremental = paths("incremental")
    retry = paths("retry")
    assert len({*initial, *incremental, *retry}) == 6
    assert initial[0].name == "initial.records.pending.jsonl"
    assert retry[1].name == "retry.records.complete.json"
