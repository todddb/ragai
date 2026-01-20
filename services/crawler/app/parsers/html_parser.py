from __future__ import annotations

from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from markdownify import markdownify

from app.parsers.types import ParsedDocument


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def parse_html(content_bytes: bytes, base_url: str) -> ParsedDocument:
    html = content_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    links: List[str] = []
    for tag in soup.find_all("a", href=True):
        links.append(urljoin(base_url, tag["href"]))

    body = soup.body or soup
    markdown = markdownify(str(body)).strip()
    text = _collapse_whitespace(body.get_text(separator=" "))

    return ParsedDocument(title=title, markdown=markdown, text_for_chunking=text, links=links)
