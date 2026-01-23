from typing import List, Optional

from pydantic import BaseModel, Field


class IntentOutput(BaseModel):
    intent_label: str
    search_queries: List[str]
    success_criteria: List[str]
    context: Optional[str] = None


class ResearchHit(BaseModel):
    doc_id: str
    chunk_id: str
    url: str
    title: str
    score: float
    text: str


class AggregatedDocument(BaseModel):
    doc_id: str
    title: str
    url: str
    best_score: float
    total_score: float = 0.0
    match_count: int = 1
    snippet: str = ""


class ResearchOutput(BaseModel):
    hits: List[ResearchHit]
    total_results: int
    docs: List[AggregatedDocument] = Field(default_factory=list)


class SynthesisOutput(BaseModel):
    draft_answer: str
    citations_used: List[str] = Field(default_factory=list)


class ValidationOutput(BaseModel):
    status: str
    final_answer: Optional[str] = None
    needs_clarification: bool
    clarifying_question: Optional[str] = None
    reasoning: Optional[str] = None


class TitleOutput(BaseModel):
    title: str
