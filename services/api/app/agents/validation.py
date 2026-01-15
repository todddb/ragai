from app.models.schemas import ValidationOutput
from app.utils.config import load_agents_config
from app.utils.ollama import call_ollama_json


async def validate_answer(question: str, draft_answer: str, research: dict) -> ValidationOutput:
    config = load_agents_config()
    system_prompt = config["agents"]["validation"]["system_prompt"]
    prompt = (
        f"{system_prompt}\n\nUser question: {question}\nDraft answer: {draft_answer}\n"
        f"Research context: {research}\n\nRespond with JSON only."
    )
    return await call_ollama_json(prompt, ValidationOutput)
