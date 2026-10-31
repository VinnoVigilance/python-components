"""Exercise labelled extraction against actual PDFs without extra dependencies."""
import pytest

from parsing.pdfParser import PdfParser

pytestmark = pytest.mark.unit


def write_pdf(path, texts):
    """Write a minimal PDF; each tuple is (x, y, text, is_label)."""
    commands = []
    for x, y, text, bold in texts:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"BT /{'F2' if bold else 'F1'} 10 Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 650 850] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    path.write_bytes(data)
    return path


@pytest.fixture
def settings():
    return {
        "mode": "labeled_blocks", "record_start": "NAME",
        "label_font_pattern": "Bold", "end_pattern": "FOOTER",
        "columns": [
            {"fields": [{"label": "NAME", "field": "name"},
                        {"label": "OFFENSE/S", "field": "offense", "required": True}]},
            {"fields": [{"label": "ALIAS", "field": "aliases", "text_mode": "stored_characters",
                         "layout_field": "alias_layout"},
                        {"label": "REWARD", "field": "reward", "pattern": r"\d+"}]},
        ],
    }


def document(dx=0, dy=0):
    texts = []
    for y, name, first, second in [(740, "Alice", "Jove", "n"), (520, "Bob", "Israel ", "Iran")]:
        for x, offset, text in [(35, 0, "NAME"), (35, -55, "OFFENSE/S"),
                                (350, 0, "ALIAS"), (350, -55, "REWARD")]:
            texts.append((x + dx, y + dy + offset, text, True))
        texts.extend([(170 + dx, y + dy, name, False),
                      (170 + dx, y + dy - 55, "Murder", False),
                      (170 + dx, y + dy - 69, "and robbery", False),
                      (445 + dx, y + dy, first, False),
                      (445 + dx, y + dy - 14, second, False),
                      (445 + dx, y + dy - 55, "50000", False)])
    texts.append((35, 30, "FOOTER", False))
    return texts


@pytest.mark.parametrize("dx,dy", [(0, 0), (27, -85)])
def test_labels_follow_shifted_layout_and_preserve_real_spaces(tmp_path, settings, dx, dy):
    path = write_pdf(tmp_path / "shifted.pdf", document(dx, dy))
    # Logical configuration order must not impose physical field/column order.
    settings["columns"].reverse()
    for column in settings["columns"]:
        column["fields"].reverse()
    rows = PdfParser().parse(path, {"parser": settings})
    assert [r["name"] for r in rows] == ["Alice", "Bob"]
    assert [r["aliases"] for r in rows] == ["Joven", "Israel Iran"]
    assert rows[0]["alias_layout"] == "Jove\nn"
    assert rows[0]["offense"] == "Murder\nand robbery"
    assert [r["source_position"] for r in rows] == [1, 2]


def test_layout_mode_keeps_visual_wrapping(tmp_path, settings):
    settings["columns"][1]["fields"][0].pop("text_mode")
    rows = PdfParser().parse(write_pdf(tmp_path / "layout.pdf", document()), {"parser": settings})
    assert rows[0]["aliases"] == "Jove\nn"


@pytest.mark.parametrize("mutation,error", [
    ("missing_label", "missing/ambiguous label"),
    ("duplicate_label", "missing/ambiguous label"),
    ("missing_value", "empty offense"),
    ("invalid_value", "invalid reward"),
    ("reordered_characters", "stored character order"),
    ("side_by_side", "side-by-side record starts"),
])
def test_rejects_lossy_or_ambiguous_documents(tmp_path, settings, mutation, error):
    texts = document()
    if mutation == "missing_label":
        texts = [t for t in texts if not (t[1] == 685 and t[2] == "OFFENSE/S")]
    elif mutation == "duplicate_label":
        texts.append((350, 710, "ALIAS", True))
    elif mutation == "missing_value":
        texts = [t for t in texts if not (t[0] == 170 and t[1] in (685, 671))]
    elif mutation == "invalid_value":
        texts = [(x, y, "unknown" if text == "50000" else text, bold) for x, y, text, bold in texts]
    elif mutation == "reordered_characters":
        first = next(t for t in texts if t[2] == "Jove")
        texts.remove(first)
        texts.append(first)  # Stored order differs from visual reading order.
    elif mutation == "side_by_side":
        texts.append((250, 740, "NAME", True))
    with pytest.raises(ValueError, match=error):
        PdfParser().parse(write_pdf(tmp_path / "bad.pdf", texts), {"parser": settings})


@pytest.mark.parametrize("count,expected_error", [(2, None), (3, "does not reconcile")])
def test_optional_document_count_reconciles(tmp_path, settings, count, expected_error):
    settings["validation"] = {"summary_pattern": r"TOTAL (?P<record_count>\d+)"}
    texts = document() + [(35, 15, f"TOTAL {count}", False)]
    path = write_pdf(tmp_path / "count.pdf", texts)
    if expected_error:
        with pytest.raises(ValueError, match=expected_error):
            PdfParser().parse(path, {"parser": settings})
    else:
        assert len(PdfParser().parse(path, {"parser": settings})) == 2


@pytest.mark.parametrize("field,value", [("field", "source_page"), ("layout_field", "aliases")])
def test_rejects_output_field_collisions(tmp_path, settings, field, value):
    settings["columns"][0]["fields"][0][field] = value
    with pytest.raises(ValueError, match="must be unique"):
        PdfParser().parse(write_pdf(tmp_path / "collision.pdf", document()), {"parser": settings})
