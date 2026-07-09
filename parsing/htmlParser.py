import json
from scrapy import Selector


class HtmlParser:

    def parse(self, file_path, config):
        """
        Public parser interface used by WatchlistPipeline.

        It is intentionally similar to TabularParser.parse(file_path, config),
        so the pipeline can call all parsers in the same way.
        """
        list_name = config.get("list_name")

        handlers = {
            "ATC-DESIGNATED-TERRORIST-INDIVIDUALS": self.parse_atc_designated_terrorist_individuals,
            "ATC-DESIGNATED-TERRORIST-GROUPS": self.parse_atc_designated_terrorist_groups,
        }

        handler = handlers.get(list_name)

        if handler is None:
            raise ValueError(f"No HTML handler found for source: {list_name}")

        return handler(file_path)

    def parse_atc_designated_terrorist_individuals(self, file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            html = f.read()

        selector = Selector(text=html)

        rows = selector.xpath('//table[@id="tablepress-33"]//tbody/tr')

        records = []

        for row in rows:
            name = row.xpath('.//td[contains(@class, "column-1")]//text()').getall()
            category = row.xpath('.//td[contains(@class, "column-2")]//text()').getall()
            resolution = row.xpath('.//td[contains(@class, "column-3")]//text()').getall()
            date_issued = row.xpath('.//td[contains(@class, "column-4")]//text()').getall()

            name = self.clean_text(" ".join(name))
            category = self.clean_text(" ".join(category))
            resolution = self.clean_text(" ".join(resolution))
            date_issued = self.clean_text(" ".join(date_issued))

            detail_url = row.xpath('.//td[contains(@class, "column-1")]//a/@href').get() or ""

            if not name:
                continue

            records.append({
                "entity_type": "Individual",
                "name": name,
                "category": category,
                "atc_resolution_no": resolution,
                "date_issued": date_issued,
                "detail_url": detail_url,
            })

        return records

    def parse_atc_designated_terrorist_groups(self, file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            html = f.read()

        selector = Selector(text=html)

        rows = selector.xpath('//table[@id="tablepress-31"]//tbody/tr')

        records = []

        for row in rows:
            name = row.xpath('.//td[contains(@class, "column-1")]//text()').getall()
            category = row.xpath('.//td[contains(@class, "column-2")]//text()').getall()
            resolution = row.xpath('.//td[contains(@class, "column-3")]//text()').getall()
            date_issued = row.xpath('.//td[contains(@class, "column-4")]//text()').getall()

            name = self.clean_text(" ".join(name))
            category = self.clean_text(" ".join(category))
            resolution = self.clean_text(" ".join(resolution))
            date_issued = self.clean_text(" ".join(date_issued))

            if not name:
                continue

            records.append({
                "entity_type": "Entity",
                "name": name,
                "category": category,
                "atc_resolution_no": resolution,
                "date_issued": date_issued,
            })

        return records

    def write_jsonl(self, records, output_path):
        with open(output_path, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def clean_text(self, value):
        if value is None:
            return ""

        return " ".join(str(value).split())


if __name__ == "__main__":
    input_html_path = "/Users/mac/Desktop/VV_Python_Project/Designated Terrorist Groups _ Anti-Terrorism Council.html"
    output_jsonl_path = "atc_designated_terrorist_groups_raw.jsonl"

    config = {
        "source_name": "ATC_DESIGNATED_TERRORIST_GROUPS"
    }

    parser = HtmlParser()

    records = parser.parse(
        file_path=input_html_path,
        config=config,
    )

    parser.write_jsonl(
        records=records,
        output_path=output_jsonl_path,
    )

    print(f"Parsed records count: {len(records)}")
    print(f"JSONL file created: {output_jsonl_path}")
