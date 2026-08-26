import pytest

from invoice_agent.tools.parsers import (
    DocumentParseError,
    UnsupportedFormatError,
    detect_format,
    load_document,
)

INVOICES = "data/invoices"


def test_detect_format_covers_all_sample_extensions():
    assert detect_format("a.txt") == "txt"
    assert detect_format("a.CSV") == "csv"
    assert detect_format("a.json") == "json"
    assert detect_format("a.xml") == "xml"
    assert detect_format("a.pdf") == "pdf"


def test_unsupported_extension_raises():
    with pytest.raises(UnsupportedFormatError, match="docx"):
        detect_format("invoice.docx")


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_document(f"{INVOICES}/does_not_exist.txt")


def test_loads_every_sample_invoice():
    import pathlib

    files = sorted(pathlib.Path(INVOICES).iterdir())
    assert len(files) == 20  # 16 invoices, some in two formats
    for f in files:
        doc = load_document(f)
        assert doc.text.strip(), f"empty text from {f}"


def test_pdf_extraction_yields_invoice_text():
    doc = load_document(f"{INVOICES}/invoice_1013.pdf")
    assert doc.format == "pdf"
    assert "INV-1013" in doc.text
    assert "22,562.80" in doc.text  # the padded grand total must survive extraction


def test_malformed_json_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"invoice_number": ')
    with pytest.raises(DocumentParseError, match="Malformed JSON"):
        load_document(bad)
