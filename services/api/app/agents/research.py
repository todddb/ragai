from app.models.schemas import ResearchOutput
from app.utils.config import load_agents_config
from app.utils.ollama import call_ollama_json


async def summarize_research(search_results: dict) -> ResearchOutput:
    config = load_agents_config()
    system_prompt = config["agents"]["research"]["system_prompt"]
    prompt = (
        f"{system_prompt}\n\nSearch results: {search_results}\n\n"
        "Return ONLY a single JSON object with double quotes and no markdown or extra text.\n"
        "Required keys and values:\n"
        '- "hits": array of objects with keys "doc_id", "chunk_id", "url", "title", "score", "text".\n'
        '- "total_results": integer.\n'
    )
    return await call_ollama_json(prompt, ResearchOutput)
