"""Student AI Assistant — FastAPI エントリポイント（仕様書 25章）。"""
from __future__ import annotations

import hashlib
import logging
import ssl
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.ai import gemini
from backend.api import chat, dashboard, google, settings, setup, voice
from backend.database import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("student-ai-assistant")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    logger.info("データベース: %s", config.DB_PATH)
    if gemini.is_configured():
        logger.info("Gemini モデル: %s", config.GEMINI_MODEL)
    else:
        logger.warning("GEMINI_API_KEY が未設定です。会話機能は利用できません。")
    if not config.GOOGLE_MAPS_API_KEY:
        logger.warning("GOOGLE_MAPS_API_KEY が未設定です。経路機能は利用できません。")
    logger.info("起動しました: http://localhost:%s", config.PORT)
    yield


app = FastAPI(
    title="Student AI Assistant",
    description="学生生活支援AIスマートスピーカー",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(chat.router)
app.include_router(voice.router)
app.include_router(settings.router)
app.include_router(dashboard.router)
app.include_router(setup.router)
app.include_router(google.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": app.version}


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(config.FRONTEND_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker() -> FileResponse:
    # Service Worker はスコープの都合上ルートから配信する必要がある
    return FileResponse(config.FRONTEND_DIR / "sw.js", media_type="application/javascript")


VERSIONED_ASSETS = ("css/style.css", "js/api.js", "js/speech.js", "js/wakeword.js", "js/app.js")


def asset_version() -> str:
    """CSS/JS の更新時刻から算出する短い版番号。

    これを各ファイルの URL に ?v= として付けることで、
    書き換えたのにブラウザが古いファイルを使い続ける事故を防ぐ。
    """
    stamps = []
    for name in VERSIONED_ASSETS:
        path = config.FRONTEND_DIR / name
        stamps.append(str(int(path.stat().st_mtime)) if path.exists() else "0")
    digest = hashlib.sha1("|".join(stamps).encode("utf-8")).hexdigest()
    return digest[:10]


def render_index() -> HTMLResponse:
    html = (config.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("__ASSET_VERSION__", asset_version())
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index() -> HTMLResponse:
    return render_index()


class AppShellStatic(StaticFiles):
    """HTML/CSS/JS は毎回サーバーへ確認しに行かせる。

    自分で書き換えながら使うアプリなので、ブラウザが古い画面を
    掴んだままにならないようにする。画像やアイコンは通常どおりキャッシュする。
    """

    REVALIDATE = (".html", ".css", ".js", ".webmanifest")

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path.endswith(self.REVALIDATE) or path in ("", "."):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


if config.FRONTEND_DIR.exists():
    app.mount("/", AppShellStatic(directory=config.FRONTEND_DIR, html=True), name="frontend")
else:  # pragma: no cover - 通常は同梱されている
    @app.get("/")
    def missing_frontend() -> JSONResponse:
        return JSONResponse({"ok": False, "error": "frontend ディレクトリが見つかりません。"}, status_code=500)


def ssl_options() -> dict:
    """証明書と鍵が対になっていることを確かめてから uvicorn へ渡す。

    対でないファイルを渡すと uvicorn は KEY_VALUES_MISMATCH で即座に落ちる。
    systemd の Restart=always の下ではそれが再起動の繰り返しになり、
    画面にすら辿り着けなくなる。ここで判断し、駄目なら理由を示して
    HTTP で起動する（音声入力は使えないが、設定画面は開ける）。
    """
    cert, key = config.HTTPS_CERT_FILE, config.HTTPS_KEY_FILE
    if not cert and not key:
        return {}
    if not (cert and key):
        logger.error("HTTPS には証明書と秘密鍵の両方が要ります。HTTP で起動します。")
        return {}

    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
    except (OSError, ssl.SSLError) as exc:
        logger.error("HTTPS を使えません: %s", exc)
        logger.error("  証明書: %s", cert)
        logger.error("  秘密鍵: %s", key)
        logger.error("  対になっていない場合は python scripts/make_cert.py で作り直せます。")
        logger.error("  HTTP で起動します（スマートフォンの音声入力は使えません）。")
        return {}

    logger.info("HTTPS で起動します（スマートフォンの音声入力に必要）")
    return {"ssl_certfile": cert, "ssl_keyfile": key}


def run() -> None:
    """python -m backend.main / スクリプトからの起動用。"""
    import uvicorn

    options: dict = {
        "host": config.HOST,
        "port": config.PORT,
        "reload": config.RELOAD,
        "log_level": "info",
    }
    options.update(ssl_options())

    uvicorn.run("backend.main:app", **options)


if __name__ == "__main__":
    run()
