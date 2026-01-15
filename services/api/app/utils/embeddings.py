from typing import List

from app.utils.config import load_system_config
from app.utils.ollama_embed import embed_text_async


async def embed_text(text: str) -> List[float]:
    config = load_system_config()
    host = config["ollama"]["host"]
    model = config["ollama"]["embedding_model"]
    return await embed_text_async(host, model, text)
