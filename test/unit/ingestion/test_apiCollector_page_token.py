"""Token-enabled API transport behavior with configurable page layouts."""

from unittest.mock import Mock, patch

import pytest

from ingestion.apiCollector.collector import _build_transport
from ingestion.apiCollector.models import ApiCollectorTask
from ingestion.apiCollector.transport import RequestsTransport


def response(*, status=200, text="", payload=None, content_type="application/json"):
    result = Mock(status_code=status, text=text, history=[], headers={"Content-Type": content_type})
    result.json.return_value = payload
    return result


def test_configured_page_token_is_used_and_refreshed_after_rejection():
    config = {
        "url": "https://example.test/api", "source_name": "EXAMPLE",
        "api_config": {"page_token": {"url": "https://example.test/page", "pattern": r'token="([^"]+)"'}},
    }
    task = ApiCollectorTask.from_config(config)
    calls = []

    def get(url, **kwargs):
        calls.append((url, dict(kwargs.get("headers", {}))))
        if url.endswith("/page"):
            return response(text='token="old"' if len(calls) == 1 else 'token="new"')
        return response(status=401) if len(calls) == 2 else response(payload=[{"id": 1}])

    with patch("ingestion.apiCollector.transport.requests.get", side_effect=get):
        with _build_transport(task) as transport:
            assert transport.get_json(task.url) == [{"id": 1}]

    assert calls[1][1]["Authorization"] == "Bearer old"
    assert calls[3][1]["Authorization"] == "Bearer new"
    assert len(calls) == 4


def test_missing_token_fails_before_api_request():
    with patch("ingestion.apiCollector.transport.requests.get", return_value=response(text="no token")) as get:
        with pytest.raises(RuntimeError, match="Unable to extract API token"):
            with RequestsTransport(page_token={"url": "https://example.test/page", "pattern": r'token="([^"]+)"'}):
                pass
    assert get.call_count == 1


def test_rejected_refreshed_token_fails_after_one_retry():
    responses = [response(text='token="first"'), response(status=403),
                 response(text='token="second"'), response(status=403)]
    with patch("ingestion.apiCollector.transport.requests.get", side_effect=responses) as get:
        with RequestsTransport(page_token={"url": "https://example.test/page", "pattern": r'token="([^"]+)"'}) as transport:
            with pytest.raises(RuntimeError, match="authentication failed"):
                transport.get_json("https://example.test/api")
    assert get.call_count == 4
