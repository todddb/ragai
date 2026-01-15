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


async def _stream_chat(conversation_id: str, user_text: str) -> AsyncGenerator[str, None]:
    history = list_messages(conversation_id)
    add_message(conversation_id, "user", {"text": user_text})

    yield _format_sse({"type": "status", "stage": "intent", "message": "Analyzing question"})
    intent = await analyze_intent(history, user_text)

    yield _format_sse({"type": "status", "stage": "research", "message": "Searching knowledge base"})
    config = load_system_config()
    qdrant = QdrantClient(url=config["qdrant"]["host"])
    collection = config["qdrant"]["collection"]
    hits = []
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
    research_output = await summarize_research({"hits": hits, "total_results": len(hits)})

    yield _format_sse({"type": "status", "stage": "synthesis", "message": "Drafting answer"})
    synthesis = await synthesize_answer(intent.model_dump(), research_output.model_dump())

    yield _format_sse({"type": "status", "stage": "validation", "message": "Verifying response"})
    validation = await validate_answer(user_text, synthesis.draft_answer, research_output.model_dump())

    if validation.needs_clarification and validation.clarifying_question:
        yield _format_sse({"type": "token", "text": validation.clarifying_question})
        yield _format_sse({"type": "done"})
        assistant_content = {
            "text": validation.clarifying_question,
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
    for token in final_answer.split(" "):
        yield _format_sse({"type": "token", "text": token + " "})
        await asyncio.sleep(0)
    yield _format_sse({"type": "done"})

    assistant_content = {
        "text": final_answer,
        "pipeline": {
            "intent": intent.model_dump(),
            "research": research_output.model_dump(),
            "synthesis": synthesis.model_dump(),
            "validation": validation.model_dump(),
        },
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
