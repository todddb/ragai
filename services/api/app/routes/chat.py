import asyncio
import json
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from qdrant_client import QdrantClient

from app.agents.intent import analyze_intent
from app.agents.research import summarize_research
from app.agents.synthesis import synthesize_answer
from app.agents.validation import validate_answer
from app.models.schemas import ResearchOutput, TitleOutput
from app.utils.config import load_system_config
from app.utils.db import (
    add_message,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    list_messages,
    update_conversation,
)
from app.utils.ollama import call_ollama_json
from app.utils.embeddings import embed_text

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/start")
async def start_conversation() -> Dict[str, str]:
    conversation_id = create_conversation()
    return {"conversation_id": conversation_id}


@router.get("/list")
async def get_conversations() -> List[Dict[str, Any]]:
    return list_conversations()


@router.get("/{conversation_id}")
async def get_conversation_detail(conversation_id: str) -> Dict[str, Any]:
    conversation = get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = list_messages(conversation_id)
    return {"conversation": conversation, "messages": messages}


@router.put("/{conversation_id}")
async def rename_conversation(conversation_id: str, payload: Dict[str, str]) -> Dict[str, str]:
    if "title" not in payload:
        raise HTTPException(status_code=400, detail="Missing title")
    update_conversation(conversation_id, payload["title"])
    return {"status": "ok"}


@router.delete("/{conversation_id}")
async def remove_conversation(conversation_id: str) -> Dict[str, str]:
    delete_conversation(conversation_id)
    return {"status": "ok"}


@router.get("/{conversation_id}/export")
async def export_conversation(conversation_id: str) -> Dict[str, Any]:
    conversation = get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = list_messages(conversation_id)
    return {
        "conversation": conversation,
        "messages": messages,
        "exported_at": datetime.utcnow().isoformat(),
    }


def _format_sse(data: Dict[str, Any]) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _dedupe_hits(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[str, Dict[str, Any]] = {}
    for hit in hits:
        doc_id = hit.get("doc_id", "")
        chunk_id = hit.get("chunk_id", "")
        key = f"{doc_id}::{chunk_id}" if doc_id or chunk_id else f"{hit.get('url', '')}::{hit.get('title', '')}"
        existing = deduped.get(key)
        if not existing or hit.get("score", 0) > existing.get("score", 0):
            deduped[key] = hit
    return sorted(deduped.values(), key=lambda item: item.get("score", 0), reverse=True)


def _chunk_text(text: str, chunk_size: int = 20) -> List[str]:
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]


def _extract_message_text(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if not content:
        return ""
    if isinstance(content, dict):
        return str(content.get("text", "")).strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return str(content).strip()
    return str(parsed.get("text", "")).strip()


@router.post("/{conversation_id}/title/auto")
async def auto_title_conversation(conversation_id: str) -> Dict[str, str]:
    conversation = get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.get("auto_titled"):
        return {"title": conversation.get("title", "")}
    if conversation.get("title") and conversation.get("title") != "New Conversation":
        return {"title": conversation.get("title", "")}

    messages = list_messages(conversation_id)
    user_message = next((msg for msg in messages if msg.get("role") == "user"), None)
    assistant_message = next((msg for msg in messages if msg.get("role") == "assistant"), None)
    if not user_message or not assistant_message:
        raise HTTPException(status_code=400, detail="Not enough messages to auto-title")

    user_text = _extract_message_text(user_message)
    assistant_text = _extract_message_text(assistant_message)
    prompt = (
        "Generate a concise 3-7 word conversation title based on this exchange.\n"
        "Return ONLY JSON: {\"title\": \"...\"}\n"
        "Rules: no punctuation, no markdown, no quotes inside the title.\n"
        f"User: {user_text}\n"
        f"Assistant: {assistant_text}"
    )

    result = await call_ollama_json(prompt, TitleOutput)
    title = " ".join(result.title.strip().split())
    if not title:
        raise HTTPException(status_code=500, detail="Failed to generate title")
    update_conversation(conversation_id, title, auto_titled=True)
    return {"title": title}


async def _stream_chat(conversation_id: str, user_text: str) -> AsyncGenerator[str, None]:
    try:
        history = list_messages(conversation_id)
        add_message(conversation_id, "user", {"text": user_text})

        yield _format_sse({"type": "status", "stage": "intent", "message": "Analyzing question"})
        intent = await analyze_intent(history, user_text)

        yield _format_sse({"type": "status", "stage": "research", "message": "Searching knowledge base"})
        config = load_system_config()
        hits = []
        try:
            qdrant = QdrantClient(url=config["qdrant"]["host"])
            collection = config["qdrant"]["collection"]
            collections = qdrant.get_collections().collections
            if any(col.name == collection for col in collections):
                for query in intent.search_queries:
                    vector = await embed_text(query)
                    search_result = qdrant.search(collection, query_vector=vector, limit=5)
                    for hit in search_result:
                        payload = hit.payload or {}
                        hits.append(
                            {
                                "doc_id": payload.get("doc_id", ""),
                                "chunk_id": payload.get("chunk_id", ""),
                                "url": payload.get("url", ""),
                                "title": payload.get("title", ""),
                                "score": hit.score,
                                "text": payload.get("text", ""),
                            }
                        )
        except Exception:
            hits = []

        try:
            research_output = await summarize_research({"hits": hits, "total_results": len(hits)})
        except Exception:
            research_output = ResearchOutput(hits=[], total_results=0)
        citations = _dedupe_hits(hits)

        yield _format_sse({"type": "status", "stage": "synthesis", "message": "Drafting answer"})
        synthesis = await synthesize_answer(intent.model_dump(), research_output.model_dump())

        yield _format_sse({"type": "status", "stage": "validation", "message": "Verifying response"})
        validation = await validate_answer(user_text, synthesis.draft_answer, research_output.model_dump())

        if validation.needs_clarification and validation.clarifying_question:
            yield _format_sse({"type": "token", "text": validation.clarifying_question})
            yield _format_sse({"type": "done"})
            assistant_content = {
                "text": validation.clarifying_question,
                "citations": citations,
                "pipeline": {
                    "intent": intent.model_dump(),
                    "research": research_output.model_dump(),
                    "synthesis": synthesis.model_dump(),
                    "validation": validation.model_dump(),
                },
                "metadata": {"processing_time_ms": 0, "model": config["ollama"]["model"]},
            }
            add_message(conversation_id, "assistant", assistant_content)
            return

        final_answer = validation.final_answer or synthesis.draft_answer
        for token in _chunk_text(final_answer):
            yield _format_sse({"type": "token", "text": token})
            await asyncio.sleep(0)
        yield _format_sse({"type": "done"})

        assistant_content = {
            "text": final_answer,
            "citations": citations,
            "pipeline": {
                "intent": intent.model_dump(),
                "research": research_output.model_dump(),
                "synthesis": synthesis.model_dump(),
                "validation": validation.model_dump(),
            },
            "metadata": {"processing_time_ms": 0, "model": config["ollama"]["model"]},
        }
        add_message(conversation_id, "assistant", assistant_content)
    except Exception as exc:
        error_text = f"⚠️ Chat pipeline failed: {exc}"
        yield _format_sse({"type": "status", "stage": "error", "message": "Chat pipeline failed"})
        yield _format_sse({"type": "token", "text": error_text})
        yield _format_sse({"type": "done"})
        config = load_system_config()
        assistant_content = {
            "text": error_text,
            "citations": [],
            "pipeline": {"error": str(exc)},
            "metadata": {"processing_time_ms": 0, "model": config["ollama"]["model"]},
        }
        add_message(conversation_id, "assistant", assistant_content)


@router.post("/{conversation_id}/message")
async def send_message(conversation_id: str, payload: Dict[str, str]) -> StreamingResponse:
    if "text" not in payload:
        raise HTTPException(status_code=400, detail="Missing text")
    if not get_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    generator = _stream_chat(conversation_id, payload["text"])
    return StreamingResponse(generator, media_type="text/event-stream")
