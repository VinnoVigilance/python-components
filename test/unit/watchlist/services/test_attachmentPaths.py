"""Offline evidence selection checks; no DB, browser, storage or OCR imports."""

import tempfile
import unittest
from pathlib import Path

from services.watchlistPipeline.attachmentPaths import list_attachment_paths


class TestAttachmentPaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="attachment paths ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "capture_001.jpg"
        self.source.write_bytes(b"poster bytes")
        self.html = self.root / "capture_001.source.html"
        self.manifest = self.root / "capture_001.manifest.json"
        self.html.write_text("<html>evidence</html>", encoding="utf-8")
        self.manifest.write_text('{"schema_version":1}', encoding="utf-8")
        self.rule = {
            "scope": "list",
            "local_files": [
                "{source_stem}.source.html",
                "{source_stem}.manifest.json",
            ],
            "required": True,
        }

    def test_selects_only_current_capture_evidence(self):
        (self.root / "capture_000.source.html").write_text("older", encoding="utf-8")
        (self.root / "capture_000.manifest.json").write_text("{}", encoding="utf-8")
        self.assertEqual(
            list_attachment_paths(self.source, self.rule),
            [self.html, self.manifest],
        )

    def test_next_capture_does_not_collect_previous_capture_files(self):
        next_source = self.root / "capture_002.jpg"
        next_source.write_bytes(b"next poster")
        next_html = self.root / "capture_002.source.html"
        next_manifest = self.root / "capture_002.manifest.json"
        next_html.write_text("new HTML", encoding="utf-8")
        next_manifest.write_text("{}", encoding="utf-8")
        self.assertEqual(
            list_attachment_paths(next_source, self.rule),
            [next_html, next_manifest],
        )

    def test_missing_required_evidence_raises(self):
        self.manifest.unlink()
        with self.assertRaises(FileNotFoundError):
            list_attachment_paths(self.source, self.rule)

    def test_optional_missing_evidence_can_be_skipped(self):
        self.manifest.unlink()
        rule = {**self.rule, "required": False}
        self.assertEqual(list_attachment_paths(self.source, rule), [self.html])

    def test_repeated_template_does_not_duplicate_attachment(self):
        rule = {"local_files": ["{source_stem}.source.html"] * 2}
        self.assertEqual(list_attachment_paths(self.source, rule), [self.html])

    def test_legacy_directory_rules_still_select_files(self):
        directory = self.root / "attachments"
        directory.mkdir()
        a = directory / "a.pdf"
        b = directory / "b.pdf"
        b.write_bytes(b"b")
        a.write_bytes(b"a")
        (directory / "subfolder").mkdir()
        self.assertEqual(
            list_attachment_paths(self.source, {"local_directory": "attachments"}),
            [a, b],
        )

    def test_missing_legacy_directory_still_returns_empty_list(self):
        self.assertEqual(
            list_attachment_paths(self.source, {"local_directory": "missing"}),
            [],
        )

    def test_paths_cannot_escape_capture_directory(self):
        for template in ("../outside.html", str(self.root.parent / "outside.html")):
            with self.subTest(template=template), self.assertRaises(ValueError):
                list_attachment_paths(self.source, {"local_files": [template]})

    def test_invalid_or_conflicting_config_is_rejected(self):
        rules = [
            {"local_files": []},
            {"local_files": "capture_001.source.html"},
            {"local_files": [""]},
            {"local_files": ["{unsupported}.html"]},
            {"local_files": ["{source_stem.__class__}.html"]},
            {"local_files": ["{source_stem}.html"], "required": "yes"},
            {"local_files": ["{source_stem}.html"], "local_directory": "."},
        ]
        for rule in rules:
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                list_attachment_paths(self.source, rule)

    def test_file_selection_does_not_modify_source_or_evidence(self):
        paths = [self.source, self.html, self.manifest]
        before = {path: path.read_bytes() for path in paths}
        list_attachment_paths(self.source, self.rule)
        self.assertEqual({path: path.read_bytes() for path in paths}, before)


if __name__ == "__main__":
    unittest.main()
