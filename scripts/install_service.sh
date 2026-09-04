#!/usr/bin/env bash
# Raspberry Pi 起動時に AI サーバーを自動起動させる（仕様書 25章）。
#
#   sudo ./scripts/install_service.sh
#
# systemd のユニットを作成し、有効化して起動する。
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="student-ai-assistant"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if [ "$(id -u)" -ne 0 ]; then
  echo "sudo を付けて実行してください: sudo ./scripts/install_service.sh" >&2
  exit 1
fi

RUN_USER="${SUDO_USER:-pi}"

echo "▸ 依存関係を用意しています…"
sudo -u "$RUN_USER" bash -c "cd '$PROJECT_DIR' && \
  { [ -d .venv ] || python3 -m venv .venv; } && \
  .venv/bin/python -m pip install --quiet --upgrade pip && \
  .venv/bin/python -m pip install --quiet -r requirements.txt"

if [ ! -f "$PROJECT_DIR/.env" ]; then
  sudo -u "$RUN_USER" cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
  echo "! .env を作成しました。GEMINI_API_KEY を設定してください。"
fi

cat > "$UNIT_PATH" <<UNIT
[Unit]
Description=Student AI Assistant
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/.venv/bin/python -m backend.main
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "▸ 自動起動を設定しました。"
echo "  状態確認: systemctl status ${SERVICE_NAME}"
echo "  ログ:     journalctl -u ${SERVICE_NAME} -f"
echo "  停止:     sudo systemctl stop ${SERVICE_NAME}"
echo "  アクセス: http://$(hostname).local:8000"
