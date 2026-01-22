from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ParsedDocument:
    title: str
    markdown: str
    text_for_chunking: str
    links: List[str] = field(default_factory=list)
    meta: Dict = field(default_factory=dict)
