from unittest.mock import MagicMock

import pytest

from services.adverseMediaPipeline import (
    mediaFileStorageService as module,
)


pytestmark = pytest.mark.unit


def test_raw_object_path_contains_content_hash(
    monkeypatch,
    tmp_path,
):
    raw_file = tmp_path / "article.html"
    raw_file.write_text("version one", encoding="utf-8")

    upload = MagicMock(return_value="stored/path")
    monkeypatch.setattr(
        module.seaweedClient,
        "upload_file",
        upload,
    )

    result = module.MediaRawService._store_file(
        source_name="DOJ_PH",
        dataset_name="DOJ_PH_NEWS",
        local_path=str(raw_file),
        file_hash="abcdef0123456789ffff",
    )

    assert result == "stored/path"
    object_path = upload.call_args.kwargs[
        "object_path"
    ]
    assert object_path.endswith(
        "/article__abcdef0123456789.html"
    )

