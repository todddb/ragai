from app.models.schemas import SynthesisOutput
from app.utils.config import load_agents_config
from app.utils.ollama import call_ollama_json


async def synthesize_answer(intent: dict, research: dict, docs: list = None) -> SynthesisOutput:
    config = load_agents_config()
    system_prompt = config["agents"]["synthesis"]["system_prompt"]

    # Check if this is a specific policy question
    intent_context = intent.get('context', '')
    is_specific_policy = 'specific_policy: true' in str(intent_context)
    byu_source = False
    if docs:
        for doc in docs[:1]:
            title = (doc.get('title') or '').lower()
            url = (doc.get('url') or '').lower()
            if 'byu' in title or 'byu.edu' in url:
                byu_source = True

    # Build citation instructions
    citation_instructions = ""
    if docs:
        citation_instructions = "\n\nAvailable sources (use these for inline citations):\n"
        for idx, doc in enumerate(docs[:6], 1):  # Only use top 6 docs
            citation_instructions += (
                f"[{idx}] Title: {doc.get('title', 'Unknown')}\n"
                f"    URL: {doc.get('url', '')}\n"
                f"    Snippet: \"{doc.get('snippet', '')[:200]}...\"\n"
            )

    policy_instruction = ""
    if is_specific_policy:
        policy_instruction = (
            "\n\nIMPORTANT - This is a SPECIFIC POLICY QUESTION:\n"
            "- Answer about the organization's specific policy, NOT a general definition\n"
            "- Use ONLY the provided documents - do not invent policy text\n"
            "- If a detail is missing, say \"Not found in provided sources.\"\n"
            "- Include inline citations using [1], [2], etc. for every sentence\n"
            "- Provide 1-3 short direct quotes (<= 25 words each) with citations\n"
            "- Use the numbered sources from the list above for your citations\n"
            "- Use this structured format:\n"
            "  Policy name: ... [1]\n"
            "  Applies to: ... [1]\n"
            "  Approval / duration: ... [1]\n"
            "  Key requirements: ... [1]\n"
            "  Links: ... [1]\n"
        )
        if byu_source:
            policy_instruction += (
                "- The top source indicates BYU; assume BYU policy context without asking which organization.\n"
            )

    prompt = (
        f"{system_prompt}\n\nIntent: {intent}\nResearch: {research}\n"
        f"{citation_instructions}"
        f"{policy_instruction}\n\n"
        "Return ONLY a single JSON object with double quotes and no markdown or extra text.\n"
        "Required keys and values:\n"
        '- "draft_answer": string (include inline citations like [1], [2] if applicable).\n'
        '- "citations_used": array of strings (list of doc_ids or sources used).\n'
    )
    return await call_ollama_json(prompt, SynthesisOutput)
