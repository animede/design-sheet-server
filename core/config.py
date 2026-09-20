# -*- coding: utf-8 -*-
"""design-sheet-server の設定(環境変数一元定義)。

本アプリはGPU処理を一切持たない。画像生成はすべて diffusers-image-server
(既定 http://127.0.0.1:8620)へHTTPで委譲する(接続先はポート可変前提、
diffusers-server CLAUDE.md 28番の流儀)。
"""
import os

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def _get(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# 生成バックエンド(diffusers-image-server)。ポートはハードコードしない。
IMAGE_SERVER_URL = _get("DS_SHEET_IMAGE_SERVER_URL", "http://127.0.0.1:8620").rstrip("/")

# ジョブ出力(画像・job.json・sheet.png・zip)の保存先
OUTPUTS_DIR = _get("DS_SHEET_OUTPUTS_DIR", os.path.join(PROJECT_ROOT, "outputs"))

# /api/edit に渡す quant(空=バックエンドの現状維持。"gguf-q4_k_m" 等で明示切替)
DEFAULT_QUANT = _get("DS_SHEET_QUANT", "")

# 変種パス(線画・部分彩色)の生成解像度。0 = 元ビュー画像のサイズに合わせる
EDIT_SIZE = int(_get("DS_SHEET_EDIT_SIZE", "0"))

# HTTPタイムアウト。model_cpu ティア(24GB級)では1枚30秒超+初回ロードがあるため長め
EDIT_TIMEOUT_S = float(_get("DS_SHEET_EDIT_TIMEOUT_S", "600"))

# charsheet ジョブのポーリング間隔
POLL_INTERVAL_S = float(_get("DS_SHEET_POLL_INTERVAL_S", "2.0"))

# バックエンドが 409(GPUビジー/別ジョブ実行中)を返したときのリトライ
BUSY_RETRIES = int(_get("DS_SHEET_BUSY_RETRIES", "60"))
BUSY_RETRY_INTERVAL_S = float(_get("DS_SHEET_BUSY_RETRY_INTERVAL_S", "10.0"))

# シート合成の1パネル幅(px)。高さはアスペクト比維持でフィット
SHEET_PANEL_PX = int(_get("DS_SHEET_PANEL_PX", "512"))

# Cloudflare R2 への一時共有。4項目がすべて設定されている場合だけUI/APIを有効化する。
# 認証情報はリポジトリへ保存せず、起動プロセスの環境変数からのみ読み込む。
R2_ENDPOINT = _get("DS_SHEET_R2_ENDPOINT", "").rstrip("/")
R2_BUCKET = _get("DS_SHEET_R2_BUCKET", "")
R2_ACCESS_KEY_ID = _get("DS_SHEET_R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = _get("DS_SHEET_R2_SECRET_ACCESS_KEY", "")
R2_URL_TTL_S = max(60, min(604800, int(_get("DS_SHEET_R2_URL_TTL_S", "86400"))))
R2_SHARE_ENABLED = all((
    R2_ENDPOINT,
    R2_BUCKET,
    R2_ACCESS_KEY_ID,
    R2_SECRET_ACCESS_KEY,
))
