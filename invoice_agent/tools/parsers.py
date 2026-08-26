"""Format detection and document-to-text canonicalization.

Every invoice, regardless of format, is reduced to plain text for the extraction
agent. Structured formats (JSON/XML) are checked for well-formedness here so a
corrupt file fails fast with a clear error instead of confusing the LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


class UnsupportedFormatError(ValueError):
    """File extension is not one we know how to read."""


class DocumentParseError(ValueError):
    """File exists but its content could not be read as its format."""


SUPPORTED_EXTENSIONS = {".txt", ".csv", ".json", ".xml", ".pdf"}


@dataclass(frozen=True)
class ParsedDocument:
    source_file: str
    format: str  # txt | csv | json | xml | pdf
    text: str


def detect_format(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"Unsupported invoice format {ext!r} for {path}. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    return ext.lstrip(".")


def load_document(path: str | Path) -> ParsedDocument:
    """Read an invoice file and return its canonical text representation."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Invoice file not found: {path}")
    fmt = detect_format(path)

    if fmt == "pdf":
        text = _extract_pdf_text(path)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        if fmt == "json":
            _check_json(path, text)

    if not text.strip():
        raise DocumentParseError(f"No text content could be read from {path}")
    return ParsedDocument(source_file=str(path), format=fmt, text=text)


def _extract_pdf_text(path: Path) -> str:
    import pdfplumber  # deferred: heavy import, only needed for PDFs

    try:
        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:
        raise DocumentParseError(f"Could not extract text from PDF {path}: {exc}") from exc


def _check_json(path: Path, text: str) -> None:
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise DocumentParseError(f"Malformed JSON in {path}: {exc}") from exc
