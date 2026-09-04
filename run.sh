#!/usr/bin/env bash
# Student AI Assistant をコマンド一つで起動する（Raspberry Pi / Linux / macOS）。
#
#   ./run.sh
#
# 仮想環境の作成、依存関係のインストール、.env の用意、サーバー起動までを行う。
# 2回目以降は変更がなければインストールを飛ばして即座に起動する。
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
STAMP="$VENV/.requirements.sha"

info()  { printf "\033[36m▸ %s\033[0m\n" "$1"; }
warn()  { printf "\033[33m! %s\033[0m\n" "$1"; }
fail()  { printf "\033[31m✗ %s\033[0m\n" "$1" >&2; exit 1; }

# --- Python の確認 ---
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi
[ -n "$PYTHON" ] || fail "Python 3.10 以上が見つかりません。sudo apt install python3 python3-venv を実行してください。"

"$PYTHON" - <<'PY' || fail "Python 3.10 以上が必要です。"
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

# --- 仮想環境 ---
if [ ! -d "$VENV" ]; then
  info "仮想環境を作成しています…"
  "$PYTHON" -m venv "$VENV" || fail "venv の作成に失敗しました。sudo apt install python3-venv を試してください。"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

# --- 依存関係（requirements.txt が変わったときだけ入れ直す）---
CURRENT_HASH="$( (cat requirements.txt; echo) | cksum | cut -d' ' -f1)"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$CURRENT_HASH" ]; then
  info "依存関係をインストールしています…（初回は数分かかります）"
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt || fail "依存関係のインストールに失敗しました。"
  echo "$CURRENT_HASH" > "$STAMP"
fi

# --- 設定ファイル ---
if [ ! -f .env ]; then
  cp .env.example .env
  warn ".env を作成しました。GEMINI_API_KEY を設定すると会話機能が使えます。"
  warn "  https://aistudio.google.com/apikey でキーを取得し、.env に貼り付けてください。"
fi

if ! grep -qE '^GEMINI_API_KEY=.+' .env; then
  warn "GEMINI_API_KEY が未設定です。画面は開きますが、AIとの会話はできません。"
fi

# --- 起動 ---
PORT="$(grep -E '^PORT=' .env | cut -d= -f2 | tr -d ' \r' || true)"
PORT="${PORT:-8000}"

info "起動します:  http://localhost:${PORT}"
for ip in $(hostname -I 2>/dev/null || true); do
  info "スマートフォンから:  http://${ip}:${PORT}"
done

exec python -m backend.main
