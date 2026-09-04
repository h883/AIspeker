"""音声合成（仕様書 6章）。

既定はスマートフォンの SpeechSynthesis で読み上げる（オフライン・無料・低遅延）。
TTS_PROVIDER=gtts にすると、サーバー側で MP3 を生成して返す。
"""
from __future__ import annotations

import io
import logging

from backend import config

logger = logging.getLogger(__name__)


class TTSUnavailable(RuntimeError):
    """サーバー側の音声合成が使えないことを表す。"""


def provider() -> str:
    return config.TTS_PROVIDER.lower()


def available() -> bool:
    if provider() != "gtts":
        return False
    try:
        import gtts  # noqa: F401
    except ImportError:
        return False
    return True


def synthesize(text: str, lang: str = "ja") -> tuple[bytes, str]:
    """音声データと MIME タイプを返す。使えない場合は TTSUnavailable。"""
    if not text.strip():
        raise TTSUnavailable("読み上げる文章が空です。")
    if not available():
        raise TTSUnavailable("サーバー側の音声合成は無効です。ブラウザの読み上げを使用します。")

    from gtts import gTTS

    buffer = io.BytesIO()
    try:
        gTTS(text=text, lang=lang).write_to_fp(buffer)
    except Exception as exc:  # noqa: BLE001 - ネットワーク断などはブラウザ読み上げへ
        logger.warning("gTTS に失敗しました: %s", exc)
        raise TTSUnavailable("音声を生成できませんでした。") from exc
    return buffer.getvalue(), "audio/mpeg"
