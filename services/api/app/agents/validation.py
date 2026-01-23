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

    citation_check = ""
    if is_specific_policy and not has_citations:
        citation_check = (
            "\n\nWARNING: This is a specific policy question but the draft answer lacks inline citations [1], [2], etc. "
            "Consider marking this as needs_clarification if the answer should be more specific and cite sources.\n"
        )

    prompt = (
        f"{system_prompt}\n\nUser question: {question}\nDraft answer: {draft_answer}\n"
        f"Research context: {research}\n"
        f"{citation_check}\n"
        "Return ONLY a single JSON object with double quotes and no markdown or extra text.\n"
        "Required keys and values:\n"
        '- "status": string (e.g., "final" or "needs_clarification").\n'
        '- "final_answer": string or null.\n'
        '- "needs_clarification": boolean.\n'
        '- "clarifying_question": string or null.\n'
        '- "reasoning": string or null.\n'
    )
    return await call_ollama_json(prompt, ValidationOutput)
