import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from scrapy.http import HtmlResponse

from ingestion.crawler.models import CrawlerTask
from ingestion.crawler.spiders.genericSpider import GenericSpider
from ingestion.crawler.storage import CrawlerStorage


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "test/fixtures/watchlist/sec_us_pause"
BASE = (
    "https://www.sec.gov/enforcement-litigation/"
    "public-alerts-unregistered-soliciting-entities"
)


class TestSecUsPauseExtraction(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        config_path = ROOT / "config/watchlistSources/sec_us_pause.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.records = []
        self.storage = CrawlerStorage(
            source_name="TEST-SEC-US",
            list_name="TEST-PAUSE",
            base_dir=self.directory.name,
        )
        self.spider = GenericSpider(
            task=CrawlerTask(
                url=BASE, source_name="TEST-SEC-US", list_name="TEST-PAUSE"
            ),
            crawler_config=config,
            storage=self.storage,
            records=self.records,
        )
        self.listing_requests = list(
            self.spider.parse(self.page("listing.html", BASE))
        )
        self.detail_requests = {
            request.url: request
            for request in self.listing_requests
            if request.callback == self.spider.parse_detail
        }

    @staticmethod
    def page(filename, url):
        return HtmlResponse(
            url=url,
            body=(FIXTURES / filename).read_bytes(),
            encoding="utf-8",
        )

    def extract(self, slug):
        url = BASE + "/" + slug
        request = self.detail_requests[url]
        return list(self.spider.parse_detail(
            self.page(slug + ".html", url),
            **request.cb_kwargs,
        ))[0]

    def test_first_listing_discovers_100_details_and_next_page(self):
        self.assertEqual(len(self.detail_requests), 100)
        following = [
            r for r in self.listing_requests
            if r.callback == self.spider.parse
        ]
        self.assertEqual(len(following), 1)
        self.assertEqual(following[0].url, BASE + "?page=1")

    def test_all_three_alert_categories_are_preserved(self):
        cases = {
            "1st-american-securities": "Unregistered Soliciting Entities",
            "12-west-capital-management": "Impersonators of Genuine Firms",
            "anti-cyber-crime-financial": "Fictitious Regulators",
        }
        for slug, expected in cases.items():
            with self.subTest(slug=slug):
                record = self.extract(slug)
                self.assertEqual(record["list"]["alert_category"], expected)

    def test_unregistered_entity_contacts_are_extracted(self):
        record = self.extract("1st-american-securities")
        detail = record["detail"]
        self.assertEqual(detail["name"], "1st American Securities")
        for value in (
            "632 Broadway",
            "(646) 313-3785",
            "(646) 224-8912",
            "http://www.firstamericansecurities.com",
        ):
            self.assertIn(value, detail["contact_text"])

    def test_impersonator_name_is_not_the_genuine_firm_name(self):
        detail = self.extract("12-west-capital-management")["detail"]
        self.assertEqual(detail["name"], "12 West Capital Management")
        self.assertIn("12 West Capital Management LP", detail["notes"])

    def test_genuine_firm_identifiers_stay_only_in_notes(self):
        detail = self.extract("12-west-capital-management")["detail"]
        for identifier in ("160197", "801-73235", "0001540531"):
            with self.subTest(identifier=identifier):
                self.assertIn(identifier, detail["notes"])
                self.assertNotIn(identifier, detail["contact_text"])
                self.assertNotIn(identifier, detail["contact_html"])
        self.assertIn("http://www.12-wcm.com", detail["contact_text"])

    def test_alert_dates_are_not_review_dates(self):
        expected = {
            "1st-american-securities": "July 25, 2019",
            "12-west-capital-management": "July 19, 2019",
            "anti-cyber-crime-financial": "July 18, 2019",
        }
        for slug, date in expected.items():
            with self.subTest(slug=slug):
                self.assertEqual(
                    self.extract(slug)["detail"]["alert_date_raw"], date
                )

    def test_fictitious_regulator_email_is_preserved(self):
        detail = self.extract("anti-cyber-crime-financial")["detail"]
        self.assertEqual(detail["name"], "Anti Cyber Crime & Financial")
        self.assertIn("antifraud@accffa.org", detail["contact_text"])
        self.assertIn("www.accffa.org", detail["contact_text"])

    def test_absent_optional_notes_do_not_capture_page_boilerplate(self):
        detail = self.extract("1st-american-securities")["detail"]
        self.assertFalse(detail["notes"])
        self.assertNotIn("More Information", detail["contact_text"])
        self.assertNotIn("PAUSE Program lists", detail["contact_text"])

    def test_contact_html_keeps_structure_for_later_parsing(self):
        detail = self.extract("1st-american-securities")["detail"]
        self.assertIn("field--name-field-public-alerts-contact",
                      detail["contact_html"])
        self.assertIn("<br", detail["contact_html"])

    def test_same_name_in_distinct_pages_keeps_distinct_records(self):
        matches = [
            r for r in self.detail_requests.values()
            if r.cb_kwargs["list_data"]["name"] == "Angle Financial Solutions"
        ]
        self.assertEqual(len(matches), 2)
        self.assertEqual(len({r.url for r in matches}), 2)
        self.assertEqual(
            len({r.cb_kwargs["record_id"] for r in matches}), 2
        )

    def test_detail_html_is_saved_for_audit(self):
        record = self.extract("12-west-capital-management")
        saved = Path(record["detail_file_path"])
        self.assertTrue(saved.is_file())
        self.assertIn("0001540531", saved.read_text(encoding="utf-8"))
        self.assertEqual(record["detail_url"],
                         BASE + "/12-west-capital-management")

    def test_last_listing_has_no_next_request(self):
        requests = list(self.spider.parse(
            self.page("last_listing.html", BASE + "?page=16")
        ))
        self.assertEqual(len(requests), 90)
        self.assertTrue(all(r.callback == self.spider.parse_detail
                            for r in requests))
        self.assertTrue(self.spider.pagination_state["complete"])


if __name__ == "__main__":
    unittest.main()

