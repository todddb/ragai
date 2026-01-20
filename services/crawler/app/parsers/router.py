from __future__ import annotations

from typing import Dict, Tuple
from urllib.parse import urlparse

from app.parsers.types import ParsedDocument

CONTENT_TYPE_MAP: Dict[str, str] = {
    "text/html": "html",
    "application/xhtml+xml": "html",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xlsx",
}

EXTENSION_MAP: Dict[str, str] = {
    ".html": "html",
    ".htm": "html",
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
}


def _normalize_content_type(content_type: str) -> str:
    return (content_type or "").split(";")[0].strip().lower()


def select_parser(content_type: str, url: str) -> str:
    normalized = _normalize_content_type(content_type)
    if normalized in CONTENT_TYPE_MAP:
        return CONTENT_TYPE_MAP[normalized]
    path = urlparse(url).path.lower()
    for ext, parser_name in EXTENSION_MAP.items():
        if path.endswith(ext):
            return parser_name
    return "html"


def parse_by_type(content_bytes: bytes, content_type: str, url: str) -> Tuple[ParsedDocument, str]:
    parser_name = select_parser(content_type, url)
    if parser_name == "html":
        from app.parsers.html_parser import parse_html

        return parse_html(content_bytes, url), parser_name
    if parser_name == "pdf":
        from app.parsers.pdf_parser import parse_pdf

        return parse_pdf(content_bytes), parser_name
    if parser_name == "docx":
        from app.parsers.docx_parser import parse_docx

        return parse_docx(content_bytes), parser_name
    if parser_name == "pptx":
        from app.parsers.pptx_parser import parse_pptx

        return parse_pptx(content_bytes), parser_name
    if parser_name == "xlsx":
        from app.parsers.xlsx_parser import parse_xlsx

        return parse_xlsx(content_bytes), parser_name
    from app.parsers.html_parser import parse_html

    return parse_html(content_bytes, url), "html"
