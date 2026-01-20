"""Ad-hoc validation for Ollama JSON schema outputs.

Usage:
  python services/api/scripts/verify_ollama_json.py
"""

import asyncio

from app.models.schemas import IntentOutput
from app.utils.ollama import call_ollama_json


async def main() -> None:
    prompt = (
        "Analyze the user question and produce intent metadata."
        "\nConversation history: []"
        "\nUser question: How do I reset my account password?"
    )
    result = await call_ollama_json(prompt, IntentOutput)
    print(result.model_dump())


if __name__ == "__main__":
    asyncio.run(main())
