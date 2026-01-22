from __future__ import annotations

import io

import pdfplumber

from app.parsers.types import ParsedDocument


def parse_pdf(content_bytes: bytes) -> ParsedDocument:
    text_parts = []
    with pdfplumber.open(io.BytesIO(content_bytes)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    text = "\n\n".join(part for part in text_parts if part)
    markdown = text.strip()
    return ParsedDocument(
        title="",
        markdown=markdown,
        text_for_chunking=" ".join(text.split()),
        meta={"page_count": len(text_parts)},
    )
