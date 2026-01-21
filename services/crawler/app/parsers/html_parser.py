from __future__ import annotations

from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from markdownify import markdownify

from app.parsers.types import ParsedDocument


BOILERPLATE_PHRASES = {
    "table of contents",
    "close menu",
    "sign in",
    "sign in to view",
    "skip to main content",
    "burger menu",
}


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def _strip_layout_elements(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(["header", "nav", "footer", "aside"]):
        tag.decompose()
    for tag in soup.find_all(attrs={"role": ["navigation", "banner", "contentinfo"]}):
        tag.decompose()


def _strip_boilerplate_sections(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(["section", "div", "nav", "aside"]):
        text = tag.get_text(separator=" ", strip=True).lower()
        if any(phrase in text for phrase in BOILERPLATE_PHRASES):
            tag.decompose()


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

    _strip_layout_elements(soup)
    _strip_boilerplate_sections(soup)

    body = soup.find("main") or soup.find("article") or soup.body or soup
    markdown = markdownify(str(body)).strip()
    text = _collapse_whitespace(body.get_text(separator=" "))

    return ParsedDocument(title=title, markdown=markdown, text_for_chunking=text, links=links)
