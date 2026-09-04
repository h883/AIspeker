"""音声 API（仕様書 6章）。ブラウザ側 Web Speech API のフォールバックとして使う。"""
from __future__ import annotations

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.audio import stt, tts

router = APIRouter(prefix="/api/voice", tags=["voice"])


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    lang: str = "ja"


@router.get("/capabilities")
def capabilities() -> dict:
    return {
        "ok": True,
        "server_stt": stt.available(),
        "server_tts": tts.available(),
        "tts_provider": tts.provider(),
    }


@router.post("/stt")
async def speech_to_text(file: UploadFile = File(...)) -> dict:
    audio = await file.read()
    if not audio:
        return {"ok": False, "error": "音声データが空です。"}
    suffix = "." + (file.filename or "audio.webm").rsplit(".", 1)[-1]
    return stt.transcribe(audio, suffix=suffix)


@router.post("/tts")
def text_to_speech(request: SpeakRequest) -> Response:
    try:
        audio, media_type = tts.synthesize(request.text, request.lang)
    except tts.TTSUnavailable as exc:
        return Response(
            content=f'{{"ok": false, "error": "{exc}"}}',
            media_type="application/json",
            status_code=503,
        )
    return Response(content=audio, media_type=media_type)
