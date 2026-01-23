from app.models.schemas import ValidationOutput
from app.utils.config import load_agents_config
from app.utils.ollama import call_ollama_json


import re
from app.models.schemas import ValidationOutput
from app.utils.config import load_agents_config
from app.utils.ollama import call_ollama_json


async def validate_answer(question: str, draft_answer: str, research: dict, intent_context: str = None) -> ValidationOutput:
    config = load_agents_config()
    system_prompt = config["agents"]["validation"]["system_prompt"]

    # Check if citations are present
    has_citations = bool(re.search(r'\[\d+\]', draft_answer))
    is_specific_policy = intent_context and 'specific_policy: true' in str(intent_context)
    sources = research.get("docs") or []
    byu_source = False
    if sources:
        top_doc = sources[0]
        title = (top_doc.get("title") or "").lower()
        url = (top_doc.get("url") or "").lower()
        if "byu" in title or "byu.edu" in url:
            byu_source = True

    paragraphs = [p.strip() for p in draft_answer.split("\n\n") if p.strip()]
    uncited_paragraphs = [p for p in paragraphs if not re.search(r'\[\d+\]', p)]

    citation_check = ""
    if is_specific_policy and not has_citations:
        citation_check = (
            "\n\nWARNING: This is a specific policy question but the draft answer lacks inline citations [1], [2], etc. "
            "Consider marking this as needs_clarification if the answer should be more specific and cite sources.\n"
        )
    if uncited_paragraphs:
        citation_check += (
            "\n\nWARNING: One or more paragraphs lack citations. Rewrite the answer so every paragraph includes "
            "at least one inline citation and remove unsupported sentences.\n"
        )

    source_context = ""
    if sources:
        source_context = "\n\nAvailable sources for grounding:\n"
        for idx, doc in enumerate(sources[:6], 1):
            source_context += (
                f"[{idx}] Title: {doc.get('title', 'Unknown')}\n"
                f"    URL: {doc.get('url', '')}\n"
                f"    Snippet: \"{doc.get('snippet', '')[:200]}...\"\n"
            )

    byu_instruction = ""
    if byu_source:
        byu_instruction = "- The top source indicates BYU; do not ask which organization.\n"

    prompt = (
        f"{system_prompt}\n\nUser question: {question}\nDraft answer: {draft_answer}\n"
        f"Research context: {research}\n"
        f"{source_context}"
        f"{citation_check}\n"
        "Requirements:\n"
        "- Ensure every paragraph has at least one citation like [1].\n"
        "- Use only the provided sources; drop or rewrite any unsupported claims.\n"
        f"{byu_instruction}"
        "- If sources are missing or weak, you may ask for clarification.\n"
        "Return ONLY a single JSON object with double quotes and no markdown or extra text.\n"
        "Required keys and values:\n"
        '- "status": string (e.g., "final" or "needs_clarification").\n'
        '- "final_answer": string or null.\n'
        '- "needs_clarification": boolean.\n'
        '- "clarifying_question": string or null.\n'
        '- "reasoning": string or null.\n'
    )
    return await call_ollama_json(prompt, ValidationOutput)
