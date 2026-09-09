#!/bin/bash
# design-sheet-server 起動スクリプト。
# 既定はプロジェクト直下の venv/。既存の venv を共有する場合は DS_SHEET_VENV で指定する。
cd "$(dirname "$0")"
VENV=${DS_SHEET_VENV:-./venv}
PORT=${DS_SHEET_PORT:-8650}
if [ ! -x "$VENV/bin/python" ]; then
  echo "venv が見つかりません: $VENV" >&2
  echo "  python3 -m venv venv && venv/bin/pip install -r requirements.txt" >&2
  echo "  または DS_SHEET_VENV=<既存venvのパス> ./run.sh" >&2
  exit 1
fi
exec "$VENV/bin/python" -m uvicorn app:app --host 0.0.0.0 --port "$PORT"
