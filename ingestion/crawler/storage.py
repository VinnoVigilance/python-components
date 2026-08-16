from datetime import datetime
from pathlib import Path


class CrawlerStorage:
    def __init__(
        self,
        source_name: str,
        list_name: str,
        base_dir: str = "data/downloads",
        detail_directory: str = "attachments/members",
    ):
        now = datetime.now()

        self.source_name = source_name
        self.list_name = list_name

        self.base_path = (
            Path(base_dir)
            / source_name
            / list_name
            / f"year={now.year}"
            / f"month={now.month:02d}"
            / f"day={now.day:02d}"
        )

        self.base_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.detail_path = (
            self.base_path
            / detail_directory
        )

        self.detail_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.source_file_path = (
            self.base_path
            / f"{list_name}.html"
        )

    def save_source_html(
        self,
        content: str,
    ) -> str:
        self.source_file_path.write_text(
            content,
            encoding="utf-8",
        )

        return str(
            self.source_file_path
        )

    def save_detail_html(
        self,
        record_id: str,
        content: str,
    ) -> str:
        file_path = (
            self.detail_path
            / f"{record_id}.html"
        )

        file_path.write_text(
            content,
            encoding="utf-8",
        )

        return str(file_path)