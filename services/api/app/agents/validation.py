from app.models.schemas import ValidationOutput
from app.utils.config import load_agents_config
from app.utils.ollama import call_ollama_json


async def validate_answer(question: str, draft_answer: str, research: dict) -> ValidationOutput:
    config = load_agents_config()
    system_prompt = config["agents"]["validation"]["system_prompt"]
    prompt = (
        f"{system_prompt}\n\nUser question: {question}\nDraft answer: {draft_answer}\n"
        f"Research context: {research}\n\n"
        "Return ONLY a single JSON object with double quotes and no markdown or extra text.\n"
        "Required keys and values:\n"
        '- "status": string (e.g., "final" or "needs_clarification").\n'
        '- "final_answer": string or null.\n'
        '- "needs_clarification": boolean.\n'
        '- "clarifying_question": string or null.\n'
        '- "reasoning": string or null.\n'
    )
    return await call_ollama_json(prompt, ValidationOutput)
