"""Gemini API クライアントと Tool Calling ループ（仕様書 7章・8章）。

SDK ではなく REST を直接叩くことで依存を減らし、Raspberry Pi でも軽量に動かす。
APIキーはこのプロセス内だけで扱い、フロントには渡さない（仕様書 21章）。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from backend import config
from backend.ai.prompts import build_system_instruction
from backend.tools import registry

logger = logging.getLogger(__name__)


class GeminiError(RuntimeError):
    """Gemini API への接続・応答に失敗したことを表す。"""


def is_configured() -> bool:
    return bool(config.GEMINI_API_KEY)


def _tools_payload() -> list[dict[str, Any]]:
    return [{"functionDeclarations": registry.TOOL_DECLARATIONS}]


def _request(payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{config.GEMINI_ENDPOINT}/models/{config.GEMINI_MODEL}:generateContent"
    try:
        response = httpx.post(
            url,
            json=payload,
            headers={
                "x-goog-api-key": config.GEMINI_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=config.GEMINI_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise GeminiError("現在AIサービスに接続できません。") from exc

    if response.status_code >= 400:
        try:
            message = response.json().get("error", {}).get("message", "")
        except ValueError:
            message = response.text[:300]
        logger.error("gemini error %s: %s", response.status_code, message)
        if response.status_code in (401, 403):
            raise GeminiError("Gemini APIキーが正しくないため応答できません。")
        if response.status_code == 429:
            raise GeminiError("AIサービスの利用制限に達しました。しばらく待ってからお試しください。")
        raise GeminiError(f"AIサービスがエラーを返しました（{message or response.status_code}）。")

    try:
        return response.json()
    except ValueError as exc:
        raise GeminiError("AIサービスの応答を解析できませんでした。") from exc


def _extract_parts(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = data.get("candidates") or []
    if not candidates:
        feedback = (data.get("promptFeedback") or {}).get("blockReason")
        if feedback:
            raise GeminiError("その内容にはお答えできません。")
        raise GeminiError("AIから応答がありませんでした。")
    return (candidates[0].get("content") or {}).get("parts") or []


def _history_to_contents(history: list[dict[str, str]]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for turn in history[-10:]:
        user_text = (turn.get("user") or "").strip()
        assistant_text = (turn.get("assistant") or "").strip()
        if user_text:
            contents.append({"role": "user", "parts": [{"text": user_text}]})
        if assistant_text:
            contents.append({"role": "model", "parts": [{"text": assistant_text}]})
    return contents


def ask(
    message: str,
    history: list[dict[str, str]] | None = None,
    context: str = "",
) -> dict[str, Any]:
    """1回のユーザー発話に対して、必要なツールを呼びながら最終回答を組み立てる。"""
    if not is_configured():
        raise GeminiError("Gemini APIキーが設定されていません。.env に GEMINI_API_KEY を設定してください。")

    contents = _history_to_contents(history or [])
    contents.append({"role": "user", "parts": [{"text": message}]})

    used_tools: list[str] = []
    tool_results: list[dict[str, Any]] = []

    for _ in range(config.MAX_TOOL_ROUNDS):
        payload = {
            "systemInstruction": build_system_instruction(context),
            "contents": contents,
            "tools": _tools_payload(),
            "generationConfig": {"temperature": 0.6, "maxOutputTokens": 1024},
        }
        data = _request(payload)
        parts = _extract_parts(data)

        calls = [part["functionCall"] for part in parts if "functionCall" in part]
        if not calls:
            text = "".join(part.get("text", "") for part in parts).strip()
            if not text:
                raise GeminiError("AIから応答がありませんでした。")
            return {"text": text, "tools": used_tools, "tool_results": tool_results}

        contents.append({"role": "model", "parts": parts})

        response_parts = []
        for call in calls:
            name = call.get("name", "")
            args = call.get("args") or {}
            result = registry.call_tool(name, args)
            used_tools.append(name)
            tool_results.append({"tool": name, "args": args, "result": result})
            response_parts.append({
                "functionResponse": {"name": name, "response": result},
            })
        contents.append({"role": "user", "parts": response_parts})

    raise GeminiError("情報の確認に時間がかかりすぎました。もう一度お試しください。")
