"""会話 API（仕様書 6章）。"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.ai import gemini
from backend.database import db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict[str, str]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    ok: bool
    text: str
    tools: list[str] = []
    tool_results: list[dict] = []


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    message = request.message.strip()
    try:
        result = gemini.ask(message, history=request.history)
    except gemini.GeminiError as exc:
        # 障害時は素直に伝える（仕様書 23章）
        return ChatResponse(ok=False, text=str(exc))
    except Exception:  # noqa: BLE001
        logger.exception("chat failed")
        return ChatResponse(ok=False, text="処理中にエラーが発生しました。もう一度お試しください。")

    db.save_conversation(message, result["text"], result["tools"])
    return ChatResponse(
        ok=True,
        text=result["text"],
        tools=result["tools"],
        tool_results=result["tool_results"],
    )


@router.get("/history")
def history(limit: int = 50) -> dict:
    return {"ok": True, "items": db.recent_conversations(min(max(limit, 1), 200))}


@router.delete("/history")
def clear_history() -> dict:
    db.clear_conversations()
    return {"ok": True}
