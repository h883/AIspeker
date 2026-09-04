"""音声認識（仕様書 6章）。

既定はスマートフォン側の Web Speech API を使うため、サーバー側 STT は任意機能。
faster-whisper が入っていればサーバー内で完結した認識ができる（外部送信なし）。
音声ファイルは処理後に削除する（仕様書 24章）。
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from backend import config

logger = logging.getLogger(__name__)

_model = None
_model_failed = False

AUDIO_DIR = config.DATA_DIR / "audio"


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return not _model_failed


def _load_model():
    global _model, _model_failed
    if _model is not None or _model_failed:
        return _model
    try:
        from faster_whisper import WhisperModel

        _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    except Exception:  # noqa: BLE001 - モデル取得失敗時はブラウザ側STTに任せる
        logger.warning("faster-whisper のロードに失敗しました。ブラウザ側の音声認識を使用します。")
        _model_failed = True
        _model = None
    return _model


def transcribe(audio_bytes: bytes, suffix: str = ".webm") -> dict[str, Any]:
    if not available():
        return {
            "ok": False,
            "error": "サーバー側の音声認識は無効です。ブラウザの音声認識をご利用ください。",
        }

    model = _load_model()
    if model is None:
        return {"ok": False, "error": "音声認識モデルを読み込めませんでした。"}

    if config.SAVE_AUDIO:
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(suffix=suffix, dir=AUDIO_DIR, delete=False)
    else:
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)

    path = Path(handle.name)
    try:
        handle.write(audio_bytes)
        handle.close()
        segments, _info = model.transcribe(str(path), language="ja", vad_filter=True)
        text = "".join(segment.text for segment in segments).strip()
        return {"ok": True, "text": text, "source": "faster-whisper"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("音声認識に失敗しました")
        return {"ok": False, "error": f"音声を認識できませんでした（{exc.__class__.__name__}）。"}
    finally:
        # 保存設定が OFF のときは必ず消す（仕様書 24章）
        if not config.SAVE_AUDIO:
            path.unlink(missing_ok=True)
