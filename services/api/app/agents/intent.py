from app.models.schemas import IntentOutput
from app.utils.config import load_agents_config
from app.utils.ollama import call_ollama_json


async def analyze_intent(conversation_history: list, user_question: str) -> IntentOutput:
    config = load_agents_config()
    system_prompt = config["agents"]["intent"]["system_prompt"]
    prompt = (
        f"{system_prompt}\n\nConversation history: {conversation_history}\n"
        f"User question: {user_question}\n\n"
        "Return ONLY a single JSON object with double quotes and no markdown or extra text.\n"
        "Required keys and values:\n"
        '- "intent_label": string describing the user intent.\n'
        '- "search_queries": array of strings.\n'
        '- "success_criteria": array of strings.\n'
        '- "context": string or null.\n\n'
        "IMPORTANT: If the user is asking about a specific named policy or the organization's official policy "
        '(e.g., "What is the remote work policy?" or "What is THE remote work policy?"), set the "context" '
        'field to a JSON-like string: "specific_policy: true". If the user is asking for a general definition '
        '(e.g., "What is a remote work policy?"), set "context" to "specific_policy: false".\n'
    )
    return await call_ollama_json(prompt, IntentOutput)
