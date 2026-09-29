"""Unit tests for Media document (PDF) links and downloads, using the real
SEC_PH_ADVISORIES config: download button, PDF viewer, both, or neither."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from scrapy.http import HtmlResponse

from ingestion.crawler.models import CrawlerTask
from ingestion.crawler.spiders.mediaSpider import MediaSpider
from ingestion.crawler.spiders.savedHtmlMediaSpider import SavedHtmlMediaSpider

pytestmark = pytest.mark.unit

MEDIA_CONFIG_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "mediaSources.yaml"
)

DETAIL_URL = "https://www.sec.gov.ph/advisories-2026/aiquest-trading/"
PDF_PATH = "/wp-content/uploads/2026/09/2026Advisory_AIQUEST-TRADING.pdf"
PDF_URL = "https://www.sec.gov.ph" + PDF_PATH
VIEWER = "/wp-content/plugins/algori-pdf-viewer/dist/web/viewer.html?file="

LAZY_VIEWER = (
    '<iframe loading="lazy" src="about:blank" class="tf_iframe_lazy '
    'wp-block-cgb-block-algori-pdf-viewer-iframe resp-iframe" '
    f'data-tf-src="{VIEWER}{PDF_PATH}"></iframe>'
)
LOADED_VIEWER = (
    '<iframe src="' + VIEWER + PDF_PATH + '" class="tf_iframe_lazy '
    'wp-block-cgb-block-algori-pdf-viewer-iframe resp-iframe"></iframe>'
)
DIRECT_VIEWER = (
    '<iframe src="' + PDF_PATH + '" class="tf_iframe_lazy '
    'wp-block-cgb-block-algori-pdf-viewer-iframe resp-iframe"></iframe>'
)
BUTTON = (
    '<div class="module module-buttons"><div class="module-buttons-item">'
    f'<a href="{PDF_PATH}" class="ui builder_button blue">Download to View File</a>'
    "</div></div>"
)
PSE_LINK = '<a href="/wp-content/uploads/2026/01/unrelated-footer.pdf">Footer</a>'


def _sec_config() -> dict:
    with MEDIA_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)["sources"]["SEC_PH_ADVISORIES"]


def _detail_page(viewer: str = "", button: str = "", body: str = "") -> HtmlResponse:
    html = f"""
    <html><head>
      <meta property="article:published_time" content="2026-09-16T03:15:59+00:00">
    </head><body>
      <h1 class="logo"></h1>
      <article id="post-150635" class="post-150635 post type-post">
      <h1 class="entry-title">AIQUEST TRADING</h1>
      <div class="module_column tb-column col4-3 last">
        <div class="module module-text"><div class="tb_text_wrap">
          <p>Date Posted: 16 September 2026</p>
        </div></div>
        <div class="module module-text"><div class="tb_text_wrap">
          {body}
          <div class="wp-block-cgb-block-algori-pdf-viewer resp-container">{viewer}</div>
        </div></div>
        {button}
      </div>
      </article>
      <footer>{PSE_LINK}</footer>
    </body></html>
    """
    return HtmlResponse(url=DETAIL_URL, body=html.encode("utf-8"), encoding="utf-8")


def _extract(response: HtmlResponse) -> dict:
    spider = MediaSpider(
        task=None,
        source_config=_sec_config(),
        storage=None,
        records=[],
        discovery_service=None,
    )
    return spider._extract_record(response)


class TestSecPdfLinkExtraction:
    def test_button_and_lazy_viewer_both_found(self):
        record = _extract(_detail_page(viewer=LAZY_VIEWER, button=BUTTON))
        assert record["PdfButtonUrl"] == PDF_URL
        assert record["PdfViewerUrl"] == PDF_PATH

    def test_lazy_viewer_only_without_button(self):
        record = _extract(_detail_page(viewer=LAZY_VIEWER))
        assert record["PdfButtonUrl"] is None
        assert record["PdfViewerUrl"] == PDF_PATH

    def test_loaded_viewer_only_without_button(self):
        record = _extract(_detail_page(viewer=LOADED_VIEWER))
        assert record["PdfButtonUrl"] is None
        assert record["PdfViewerUrl"] == PDF_PATH

    def test_viewer_pointing_straight_at_the_pdf(self):
        record = _extract(_detail_page(viewer=DIRECT_VIEWER))
        assert record["PdfButtonUrl"] is None
        assert record["PdfViewerUrl"] == PDF_PATH

    def test_button_only_without_viewer(self):
        record = _extract(_detail_page(button=BUTTON))
        assert record["PdfButtonUrl"] == PDF_URL
        assert record["PdfViewerUrl"] is None

    def test_no_pdf_ignores_unrelated_page_links(self):
        record = _extract(_detail_page())
        assert record["PdfButtonUrl"] is None
        assert record["PdfViewerUrl"] is None

    def test_post_id_title_date_and_html_body(self):
        record = _extract(
            _detail_page(viewer=LOADED_VIEWER, body="<div>INVESTOR ALERT text</div>")
        )
        assert record["PostId"] == "150635"
        assert record["Title"] == "AIQUEST TRADING"
        assert record["date.originalValue"] == "2026-09-16T03:15:59+00:00"
        assert record["HtmlBodyText"] == "INVESTOR ALERT text"


def _spider(tmp_path, document_download: dict) -> SavedHtmlMediaSpider:
    source_config = {**_sec_config(), "document_download": document_download}
    task = CrawlerTask(
        url=source_config["url"],
        source_name=source_config["source_name"],
        list_name=source_config["dataset_name"],
        source_config=source_config,
        download_dir=str(tmp_path),
    )
    return SavedHtmlMediaSpider(
        task=task,
        source_config=source_config,
        storage=None,
        records=[],
        discovery_service=MagicMock(),
    )


def _result(button=None, viewer=None) -> dict:
    return {
        "record_key": "SEC_PH|SEC_PH_ADVISORIES|advisories-2026/aiquest-trading",
        "is_known": False,
        "detail_file_path": "detail.html",
        "extracted": {
            "SourceURL": DETAIL_URL,
            "PdfButtonUrl": button,
            "PdfViewerUrl": viewer,
        },
    }


def _fetcher() -> MagicMock:
    fetcher = MagicMock()
    fetcher.session_headers.return_value = {"User-Agent": "UA", "Cookie": "cf_clearance=x"}
    return fetcher


CONFIG = {
    "required": True,
    "store_as_record_file": True,
    "url_fields": ["PdfButtonUrl", "PdfViewerUrl"],
}
DOWNLOAD = "ingestion.crawler.spiders.savedHtmlMediaSpider.download"


class TestDocumentDownload:
    @pytest.mark.parametrize(
        "button, viewer",
        [
            (PDF_URL, None),
            (None, PDF_PATH),
            (PDF_URL, PDF_PATH),
        ],
        ids=["button_only", "viewer_only", "button_and_viewer"],
    )
    def test_any_link_source_downloads_the_pdf_once(self, tmp_path, button, viewer):
        spider = _spider(tmp_path, CONFIG)
        result = _result(button=button, viewer=viewer)

        with patch(DOWNLOAD, return_value="saved.pdf") as download:
            spider._download_documents(result=result, fetcher=_fetcher())

        download.assert_called_once()
        task = download.call_args.args[0]
        assert task.url == PDF_URL
        assert task.filename.endswith("_0.pdf")
        assert task.headers == {"User-Agent": "UA", "Cookie": "cf_clearance=x"}
        assert task.download_dir == str(tmp_path)
        assert result["extracted"]["DocumentUrls"] == [PDF_URL]
        assert result["extracted"]["DocumentFilePaths"] == ["saved.pdf"]
        assert not result.get("failed")

    def test_pdf_becomes_the_record_file(self, tmp_path):
        spider = _spider(tmp_path, CONFIG)
        result = _result(viewer=PDF_PATH)

        with patch(DOWNLOAD, return_value="saved.pdf"):
            spider._download_documents(result=result, fetcher=_fetcher())

        assert result["detail_file_path"] == "saved.pdf"
        assert result["file_url"] == PDF_URL

    def test_html_stays_record_file_when_not_configured(self, tmp_path):
        spider = _spider(tmp_path, {**CONFIG, "store_as_record_file": False})
        result = _result(viewer=PDF_PATH)

        with patch(DOWNLOAD, return_value="saved.pdf"):
            spider._download_documents(result=result, fetcher=_fetcher())

        assert result["detail_file_path"] == "detail.html"
        assert "file_url" not in result

    def test_different_links_download_each_file(self, tmp_path):
        spider = _spider(tmp_path, CONFIG)
        other = "/wp-content/uploads/2026/09/other.pdf"
        result = _result(button=PDF_URL, viewer=other)

        with patch(DOWNLOAD, side_effect=["first.pdf", "second.pdf"]) as download:
            spider._download_documents(result=result, fetcher=_fetcher())

        assert [call.args[0].filename[-6:] for call in download.call_args_list] == [
            "_0.pdf",
            "_1.pdf",
        ]
        assert result["extracted"]["DocumentFilePaths"] == ["first.pdf", "second.pdf"]
        assert result["detail_file_path"] == "first.pdf"

    def test_no_link_fails_the_record(self, tmp_path):
        spider = _spider(tmp_path, CONFIG)
        result = _result()

        with patch(DOWNLOAD) as download:
            spider._download_documents(result=result, fetcher=_fetcher())

        download.assert_not_called()
        assert result["failed"] is True
        assert result["error_stage"] == "DOCUMENT_FETCH"
        assert result["detail_file_path"] == "detail.html"

    def test_download_error_fails_the_record(self, tmp_path):
        spider = _spider(tmp_path, CONFIG)
        result = _result(viewer=PDF_PATH)

        with patch(DOWNLOAD, side_effect=RuntimeError("403")):
            spider._download_documents(result=result, fetcher=_fetcher())

        assert result["failed"] is True
        assert result["error_stage"] == "DOCUMENT_FETCH"
        assert result["detail_file_path"] == "detail.html"

    def test_no_document_config_does_nothing(self, tmp_path):
        spider = _spider(tmp_path, {})
        spider.source_config.pop("document_download")
        result = _result(viewer=PDF_PATH)

        with patch(DOWNLOAD) as download:
            spider._download_documents(result=result, fetcher=_fetcher())

        download.assert_not_called()
        assert "DocumentUrls" not in result["extracted"]
