"""Gemini API クライアントと Tool Calling ループ（仕様書 7章・8章）。

SDK ではなく REST を直接叩くことで依存を減らし、Raspberry Pi でも軽量に動かす。
APIキーはこのプロセス内だけで扱い、フロントには渡さない（仕様書 21章）。
"""
from __future__ import annotations

import logging
import re
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


def _error_message(response: httpx.Response) -> str:
    try:
        return response.json().get("error", {}).get("message", "") or str(response.status_code)
    except ValueError:
        return response.text[:200] or str(response.status_code)


def explain_error(status: int, message: str) -> str:
    """Gemini のエラーを、次に何をすればよいか分かる日本語にする。

    同じ 403 でも「キーが違う」のか「プロジェクトが許可されていない」のかで
    対処がまったく変わるため、原因ごとに案内を変える。
    """
    lowered = message.lower()

    if "denied access" in lowered or "project has been denied" in lowered:
        return (
            "APIキー自体は有効ですが、そのキーのGoogleプロジェクトがGemini APIの利用を"
            "許可されていません。学校や会社のGoogleアカウントで作ったキーだと、"
            "管理者がGenerative AIを制限していることがあります。"
            "個人のGoogleアカウントでAI Studioから新しいキーを作り直してみてください。"
        )
    if "has not been used in project" in lowered or "service_disabled" in lowered:
        return (
            "そのプロジェクトでGenerative Language APIが有効になっていません。"
            "Google Cloudコンソールで有効化するか、AI Studioでキーを作り直してください。"
        )
    if "user location" in lowered or "location is not supported" in lowered:
        return "現在の地域からはGemini APIを利用できません。"
    if "expired" in lowered:
        return "APIキーの有効期限が切れています。新しいキーを作り直してください。"
    if "api key not valid" in lowered or "invalid api key" in lowered or "api_key_invalid" in lowered:
        return "APIキーが正しくありません。余分な空白が入っていないか確認してください。"
    if status == 429 or "quota" in lowered or "rate limit" in lowered:
        return "利用制限に達しています。しばらく待ってから試してください。"
    if status in (401, 403):
        return f"Gemini の利用を許可されませんでした（{message}）。"
    return f"Gemini がエラーを返しました（{message}）。"


# 会話に使えないモデル（埋め込み・画像生成・音声など）を名前で除く
_NON_CHAT_HINTS = ("embedding", "embed", "aqa", "imagen", "veo", "-tts", "image-generation")


def _is_chat_model(item: dict[str, Any]) -> bool:
    name = item.get("name", "").removeprefix("models/")
    if not name or any(hint in name for hint in _NON_CHAT_HINTS):
        return False
    return "generateContent" in (item.get("supportedGenerationMethods") or [])


_VERSION_RE = re.compile(r"(\d+)\.(\d+)")

# 名前に含まれていたら「試験的なので後回し」と判断する語
_UNSTABLE_HINTS = ("preview", "experimental", "-exp", "latest")


def _model_version(name: str) -> float:
    """モデル名からバージョンを取り出す。gemini-3.6-flash なら 3.6。"""
    match = _VERSION_RE.search(name)
    if not match:
        return 0.0
    return int(match.group(1)) + int(match.group(2)) / 100


def _model_rank(name: str) -> tuple[int, float, int, str]:
    """おすすめ順に並べる。

    Gemini は古いモデルを新規ユーザーに提供しなくなることがあるため、
    まず新しいバージョンを優先する。同じバージョンなら応答の速い flash を選ぶ。
    """
    unstable = 1 if any(hint in name for hint in _UNSTABLE_HINTS) else 0
    if "flash-lite" in name:
        family = 1
    elif "flash" in name:
        family = 0
    elif "pro" in name:
        family = 2
    else:
        family = 3
    # バージョンは新しい順にしたいので符号を反転させる
    return (unstable, -_model_version(name), family, name)


def list_models(api_key: str = "") -> dict[str, Any]:
    """このキーで実際に使えるモデルの一覧を取得する。

    使えないモデルを選ばせて後から 404 になるのを防ぐため、
    セットアップ画面の選択肢はこの結果から作る。
    """
    key = (api_key or config.GEMINI_API_KEY).strip()
    if not key:
        return {"ok": False, "error": "APIキーが入力されていません。"}

    models: list[dict[str, Any]] = []
    page_token = ""
    try:
        for _ in range(5):  # 念のためページ数に上限を設ける
            params = {"pageSize": 200}
            if page_token:
                params["pageToken"] = page_token
            response = httpx.get(
                f"{config.GEMINI_ENDPOINT}/models",
                params=params,
                headers={"x-goog-api-key": key},
                timeout=20,
            )
            if response.status_code != 200:
                return {"ok": False,
                        "error": explain_error(response.status_code, _error_message(response))}

            payload = response.json()
            for item in payload.get("models") or []:
                if not _is_chat_model(item):
                    continue
                name = item["name"].removeprefix("models/")
                models.append({
                    "name": name,
                    "label": item.get("displayName") or name,
                })
            page_token = payload.get("nextPageToken") or ""
            if not page_token:
                break
    except (httpx.HTTPError, ValueError):
        return {"ok": False, "error": "Gemini に接続できませんでした。ネットワークを確認してください。"}

    if not models:
        return {"ok": False, "error": "このキーで使える会話モデルが見つかりませんでした。"}

    models.sort(key=lambda item: _model_rank(item["name"]))
    return {"ok": True, "models": models, "recommended": models[0]["name"]}


def _probe_model(key: str, model: str) -> dict[str, Any]:
    """そのモデルで実際に応答が返るかを1往復だけ試す。

    fatal=True は「モデルを変えても解決しない」という意味。
    """
    url = f"{config.GEMINI_ENDPOINT}/models/{model}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
        "generationConfig": {"maxOutputTokens": 8},
    }
    try:
        response = httpx.post(
            url,
            json=payload,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            timeout=20,
        )
    except httpx.HTTPError:
        return {"ok": False, "fatal": True,
                "error": "Gemini に接続できませんでした。ネットワークを確認してください。"}

    if response.status_code == 200:
        return {"ok": True}

    message = _error_message(response)
    lowered = message.lower()

    # モデルを変えれば直る見込みがあるのは、そのモデル固有の問題だけ
    model_specific = (
        "model" in lowered
        and ("not found" in lowered or "no longer available" in lowered
             or "not supported" in lowered or "does not exist" in lowered)
    )
    if response.status_code == 404 or model_specific:
        return {"ok": False, "fatal": False, "error": message}

    # キーやプロジェクトの問題は、どのモデルを選んでも同じ結果になる
    return {"ok": False, "fatal": True,
            "error": explain_error(response.status_code, message)}


def verify_key(api_key: str = "", model: str = "") -> dict[str, Any]:
    """セットアップ画面の接続テスト用。

    まずモデル一覧でキーの有効性を確かめ、次に実際に応答するモデルを探す。
    一覧に載っていても新規ユーザーには提供終了、という場合があるため、
    候補を順に試して最初に通ったものを採用する。
    """
    key = (api_key or config.GEMINI_API_KEY).strip()
    if not key:
        return {"ok": False, "error": "APIキーが入力されていません。"}

    listing = list_models(key)
    if not listing["ok"]:
        return listing

    available = [item["name"] for item in listing["models"]]
    requested = (model or config.GEMINI_MODEL).strip()

    # 指定されたモデルを最優先に、あとはおすすめ順で試す
    candidates = [requested] if requested in available else []
    candidates += [name for name in available if name not in candidates]

    failures: list[str] = []
    for candidate in candidates[:5]:
        result = _probe_model(key, candidate)
        if result["ok"]:
            return {
                "ok": True,
                "model": candidate,
                "models": listing["models"],
                "recommended": candidate,
                "switched": bool(requested and candidate != requested),
                "requested": requested,
                "message": "接続できました。",
            }
        if result.get("fatal"):
            return {"ok": False, "models": listing["models"], "error": result["error"]}
        failures.append(f"{candidate}: {result['error']}")

    return {
        "ok": False,
        "models": listing["models"],
        "recommended": listing["recommended"],
        "error": "使えるモデルが見つかりませんでした。" + (failures[0] if failures else ""),
    }


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
        message = _error_message(response)
        logger.error("gemini error %s: %s", response.status_code, message)
        raise GeminiError(explain_error(response.status_code, message))

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
