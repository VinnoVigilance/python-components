"""Build raw records from saved OCR geometry and a declarative layout.

No file loading, network access, OCR inference, identity generation or business
status interpretation occurs here. The same extractor accepts image/PDF pages.
"""

import hashlib
import json
import math
import re
from copy import deepcopy
from numbers import Real


class OcrLayoutError(ValueError):
    """The layout or OCR document cannot produce unambiguous raw records."""


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise OcrLayoutError(f"{label} must be a finite number.")
    return float(value)


def _integer(value, label):
    if type(value) is not int or value < 1:
        raise OcrLayoutError(f"{label} must be a positive integer.")
    return value


def _size(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise OcrLayoutError(f"{label} must contain width and height.")
    return tuple(_integer(item, label) for item in value)


def _box(value, width, height, label):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise OcrLayoutError(f"{label} must contain left, top, right, bottom.")
    left, top, right, bottom = [_number(v, label) for v in value]
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise OcrLayoutError(f"{label} must be non-empty and inside its reference area.")
    return left, top, right, bottom


def _keys(value, allowed, label):
    if not isinstance(value, dict):
        raise OcrLayoutError(f"{label} must be an object.")
    unknown = set(value) - set(allowed)
    if unknown:
        raise OcrLayoutError(f"Unknown {label} keys: {sorted(unknown)}")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise OcrLayoutError(f"{label} must be non-empty text.")
    return value


def _contains(box, center):
    return box[0] <= center[0] < box[2] and box[1] <= center[1] < box[3]


class OcrRecordExtractor:
    def __init__(self, layout):
        self.layout = deepcopy(layout)
        self._validate_layout()
        try:
            canonical = json.dumps(
                self.layout, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise OcrLayoutError("Layout must be JSON-compatible.") from exc
        self.layout_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _validate_layout(self):
        cfg = self.layout
        _keys(cfg, {
            "schema_version", "layout_id", "layout_version", "description",
            "require_all_pages", "expected_page_count", "fields", "pages",
        }, "layout")
        if type(cfg.get("schema_version")) is not int or cfg["schema_version"] != 1:
            raise OcrLayoutError("Unsupported layout schema_version.")
        _text(cfg.get("layout_id"), "layout_id")
        _integer(cfg.get("layout_version"), "layout_version")
        if type(cfg.get("require_all_pages", True)) is not bool:
            raise OcrLayoutError("require_all_pages must be boolean.")
        if "expected_page_count" in cfg:
            _integer(cfg["expected_page_count"], "expected_page_count")
        fields = cfg.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise OcrLayoutError("Layout requires non-empty fields.")
        for name, field in fields.items():
            if (
                not isinstance(name, str)
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) is None
                or name == "raw_text"
            ):
                raise OcrLayoutError(f"Invalid or reserved output field: {name!r}")
            _keys(field, {"box", "mode", "required", "separator", "review_below_score"},
                  f"field {name}")
            _box(field.get("box"), 1, 1, f"field {name} relative box")
            if field.get("mode", "single") not in ("single", "join", "list"):
                raise OcrLayoutError(f"Unsupported mode for field {name}.")
            if type(field.get("required", False)) is not bool:
                raise OcrLayoutError(f"required must be boolean for field {name}.")
            if not isinstance(field.get("separator", "\n"), str):
                raise OcrLayoutError(f"separator must be text for field {name}.")
            if "review_below_score" in field:
                score = _number(field["review_below_score"], "review_below_score")
                if not 0 <= score <= 1:
                    raise OcrLayoutError("review_below_score must lie between 0 and 1.")

        pages = cfg.get("pages")
        if not isinstance(pages, list) or not pages:
            raise OcrLayoutError("Layout requires non-empty pages.")
        page_numbers = set()
        for page in pages:
            _keys(page, {"page_number", "reference_size", "aspect_ratio_tolerance",
                         "anchors", "record_regions"}, "layout page")
            number = _integer(page.get("page_number"), "page_number")
            if number in page_numbers:
                raise OcrLayoutError("Duplicate layout page_number.")
            page_numbers.add(number)
            width, height = _size(page.get("reference_size"), "reference_size")
            tolerance = _number(page.get("aspect_ratio_tolerance", 0.01),
                                "aspect_ratio_tolerance")
            if not 0 <= tolerance <= 0.1:
                raise OcrLayoutError("aspect_ratio_tolerance must lie between 0 and 0.1.")
            anchors = page.get("anchors", [])
            if not isinstance(anchors, list):
                raise OcrLayoutError("anchors must be a list.")
            for anchor in anchors:
                _keys(anchor, {"box", "text_equals", "case_sensitive"}, "anchor")
                _box(anchor.get("box"), width, height, "anchor box")
                _text(anchor.get("text_equals"), "anchor text_equals")
                if type(anchor.get("case_sensitive", True)) is not bool:
                    raise OcrLayoutError("anchor case_sensitive must be boolean.")
            regions = page.get("record_regions")
            if not isinstance(regions, list) or not regions:
                raise OcrLayoutError("Every layout page requires record_regions.")
            keys, boxes = set(), []
            for region in regions:
                _keys(region, {"key", "box"}, "record region")
                key = _text(region.get("key"), "region key")
                if key in keys:
                    raise OcrLayoutError("Duplicate region key on a page.")
                keys.add(key)
                box = _box(region.get("box"), width, height, "record region box")
                for previous in boxes:
                    if (max(previous[0], box[0]) < min(previous[2], box[2])
                            and max(previous[1], box[1]) < min(previous[3], box[3])):
                        raise OcrLayoutError("Record regions must not overlap.")
                boxes.append(box)

    def extract(self, document):
        """Return all raw records, or raise without returning partial results."""
        pages = self._validate_document(document)
        if set(pages) != {p["page_number"] for p in self.layout["pages"]}:
            raise OcrLayoutError("OCR pages and configured layout pages must match exactly.")
        if ("expected_page_count" in self.layout
                and document["document_page_count"] != self.layout["expected_page_count"]):
            raise OcrLayoutError("Document page count differs from expected_page_count.")
        if self.layout.get("require_all_pages", True) and not document["all_pages_processed"]:
            raise OcrLayoutError("Layout requires a complete OCR document.")

        records = []
        for rule in self.layout["pages"]:
            page = pages[rule["page_number"]]
            width, height = page["image_size"]
            ref_width, ref_height = rule["reference_size"]
            ratio_error = abs((width / height) / (ref_width / ref_height) - 1)
            if ratio_error > rule.get("aspect_ratio_tolerance", 0.01) + 1e-12:
                raise OcrLayoutError(f"Aspect ratio changed on page {page['page_number']}.")
            sx, sy = width / ref_width, height / ref_height

            def scaled(box):
                return [box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy]

            prepared = self._prepare_lines(page)
            for anchor in rule.get("anchors", []):
                selected = self._select(prepared, scaled(anchor["box"]))
                actual = " ".join(" ".join(line["text"] for line in selected).split())
                expected = " ".join(anchor["text_equals"].split())
                if not anchor.get("case_sensitive", True):
                    actual, expected = actual.casefold(), expected.casefold()
                if actual != expected:
                    raise OcrLayoutError(f"Anchor mismatch on page {page['page_number']}.")

            for region in rule["record_regions"]:
                area = scaled(region["box"])
                selected = self._select(prepared, area)
                record, evidence, flags = {}, {}, []
                for name, field in self.layout["fields"].items():
                    left, top, right, bottom = field["box"]
                    field_area = [
                        area[0] + left * (area[2] - area[0]),
                        area[1] + top * (area[3] - area[1]),
                        area[0] + right * (area[2] - area[0]),
                        area[1] + bottom * (area[3] - area[1]),
                    ]
                    matches = self._select(prepared, field_area)
                    matches = [line for line in matches if line["text"].strip()]
                    label = f"page {page['page_number']}, region {region['key']}, field {name}"
                    if not matches and field.get("required", False):
                        raise OcrLayoutError(f"Missing required text: {label}.")
                    mode = field.get("mode", "single")
                    if mode == "single" and len(matches) > 1:
                        raise OcrLayoutError(f"Ambiguous text (multiple lines): {label}.")
                    texts = [line["text"] for line in matches]
                    if mode == "list":
                        record[name] = texts
                    elif not texts:
                        record[name] = None
                    elif mode == "join":
                        record[name] = field.get("separator", "\n").join(texts)
                    else:
                        record[name] = texts[0]
                    threshold = field.get("review_below_score")
                    if threshold is not None:
                        low = [line["line_number"] for line in matches if line["score"] < threshold]
                        if low:
                            flags.append({"field": name, "reason": "low_ocr_score",
                                          "line_numbers": low, "threshold": threshold})
                    evidence[name] = {"region": field_area, "lines": deepcopy(matches)}

                if not document["all_pages_processed"]:
                    flags.append({"reason": "partial_document"})
                record["raw_text"] = "\n".join(line["text"] for line in selected)
                record["_ocr"] = {
                    "source_sha256": document["source_sha256"],
                    "source_path": document.get("source_path"),
                    "document_page_count": document["document_page_count"],
                    "all_pages_processed": document["all_pages_processed"],
                    "layout_id": self.layout["layout_id"],
                    "layout_version": self.layout["layout_version"],
                    "layout_sha256": self.layout_sha256,
                    "page_number": page["page_number"],
                    "coordinate_space": page["coordinate_space"],
                    "image_size": list(page["image_size"]),
                    "region_key": region["key"],
                    "region": area,
                    "pixel_sha256": page.get("pixel_sha256"),
                    "engine": page.get("engine"),
                    "engine_version": page.get("engine_version"),
                    "runtime_version": page.get("runtime_version"),
                    "settings": deepcopy(page.get("settings")),
                    "rendering": deepcopy(page.get("rendering")),
                    "raw_lines": deepcopy(selected),
                    "fields": evidence,
                    "needs_review": bool(flags),
                    "review_flags": flags,
                }
                records.append(record)
        return records

    @staticmethod
    def _validate_document(document):
        if not isinstance(document, dict) or type(document.get("schema_version")) is not int:
            raise OcrLayoutError("Invalid OCR document schema.")
        if document["schema_version"] != 1:
            raise OcrLayoutError("Unsupported OCR document schema_version.")
        digest = document.get("source_sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise OcrLayoutError("OCR document requires a source_sha256 fingerprint.")
        total = _integer(document.get("document_page_count"), "document_page_count")
        pages = document.get("pages")
        if not isinstance(pages, list) or not pages:
            raise OcrLayoutError("OCR document requires non-empty pages.")
        if (type(document.get("processed_page_count")) is not int
                or document["processed_page_count"] != len(pages)):
            raise OcrLayoutError("processed_page_count disagrees with OCR pages.")
        numbers = []
        for page in pages:
            if not isinstance(page, dict):
                raise OcrLayoutError("OCR page must be an object.")
            number = _integer(page.get("page_number"), "OCR page_number")
            if number > total or number in numbers:
                raise OcrLayoutError("Invalid or duplicate OCR page_number.")
            numbers.append(number)
            size = _size(page.get("image_size"), "OCR image_size")
            if page.get("coordinate_space") != "upright_image_pixels":
                raise OcrLayoutError("Unsupported OCR coordinate_space.")
            if page.get("region") != [0, 0, size[0], size[1]]:
                raise OcrLayoutError("Extractor requires full-page OCR coverage.")
            if type(page.get("schema_version")) is not int or page["schema_version"] != 1:
                raise OcrLayoutError("Unsupported OCR page schema_version.")
            if page.get("status") not in ("TEXT_FOUND", "NO_TEXT"):
                raise OcrLayoutError("Invalid OCR page status.")
        selected = document.get("selected_pages")
        if (not isinstance(selected, list) or any(type(n) is not int for n in selected)
                or selected != numbers):
            raise OcrLayoutError("selected_pages disagrees with OCR pages.")
        if type(document.get("all_pages_processed")) is not bool or (
            document["all_pages_processed"] != (len(numbers) == total)
        ):
            raise OcrLayoutError("Invalid all_pages_processed coverage flag.")
        return dict(zip(numbers, pages))

    @staticmethod
    def _prepare_lines(page):
        lines = page.get("lines")
        if (not isinstance(lines, list) or type(page.get("line_count")) is not int
                or page["line_count"] != len(lines)):
            raise OcrLayoutError("OCR line_count disagrees with lines.")
        if bool(lines) != (page["status"] == "TEXT_FOUND"):
            raise OcrLayoutError("OCR page status disagrees with lines.")
        prepared, numbers = [], set()
        for line in lines:
            if not isinstance(line, dict) or not isinstance(line.get("text"), str):
                raise OcrLayoutError("OCR lines require raw string text.")
            number = _integer(line.get("line_number"), "line_number")
            if number in numbers:
                raise OcrLayoutError("Duplicate OCR line_number.")
            numbers.add(number)
            score = _number(line.get("score"), "OCR score")
            if not 0 <= score <= 1:
                raise OcrLayoutError("OCR score must lie between 0 and 1.")
            box = line.get("box")
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                raise OcrLayoutError("OCR box requires four points.")
            points = []
            for point in box:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise OcrLayoutError("OCR box points require x and y.")
                points.append([_number(point[0], "OCR x"), _number(point[1], "OCR y")])
            center = (sum(p[0] for p in points) / 4, sum(p[1] for p in points) / 4)
            prepared.append((line, center, (min(p[1] for p in points), min(p[0] for p in points))))
        return sorted(prepared, key=lambda item: item[2])

    @staticmethod
    def _select(prepared, box):
        return [line for line, center, _ in prepared if _contains(box, center)]
