from app.models.schemas import SynthesisOutput
from app.utils.config import load_agents_config
from app.utils.ollama import call_ollama_json


async def synthesize_answer(intent: dict, research: dict) -> SynthesisOutput:
    config = load_agents_config()
    system_prompt = config["agents"]["synthesis"]["system_prompt"]
    prompt = (
        f"{system_prompt}\n\nIntent: {intent}\nResearch: {research}\n\nRespond with JSON only."
    )
    return await call_ollama_json(prompt, SynthesisOutput)
