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


class ResearchOutput(BaseModel):
    hits: List[ResearchHit]
    total_results: int


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
