# -*- coding: utf-8 -*-
"""design-sheet-server — マルチキャラクターシート作成アプリ。

1枚のキャラ画像から「表現モード(リアル/アニメ/部分彩色/イラスト/線画/
ちびキャラ) × 多視点 × 表示サイズ」のデザインシートを生成する。
GPU処理は diffusers-image-server(/api/edit・
/api/charsheet)へHTTP委譲し、本アプリはジョブ管理・2パス部分彩色の
オーケストレーション・PILシート合成・UIのみを持つ(GPU/モデルロード無し)。

起動: ./run.sh  (既定 port 8650、venv は diffusers-server と共有)
"""
import io
import os
import re

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

from core import client, config, jobs
from core.prompts import (
    DEFAULT_PARTIAL_COLOR,
    DEFAULT_PARTIAL_TARGET,
    MODE_KEYS,
    MODE_LABELS,
    SIZE_KEYS,
    SIZE_LABELS,
    VARIANT_LABELS,
    VIEW_LABELS,
)

app = FastAPI(title="design-sheet-server")

os.makedirs(config.OUTPUTS_DIR, exist_ok=True)

_SAFE_ID = re.compile(r"^[0-9a-f]{12}$")
_SAFE_IMAGE = re.compile(r"^[a-z0-9_]+$")


def _check_job_id(job_id: str):
    if not _SAFE_ID.match(job_id):
        raise HTTPException(status_code=404, detail="不正なジョブIDです")


def _image_response(content: bytes, media_type: str):
    from fastapi.responses import Response
    return Response(content=content, media_type=media_type)


@app.get("/api/fetch-image")
async def fetch_image(url: str):
    """他タブからD&Dされた画像URLの取り込み(CORS回避のローカル専用プロキシ)。"""
    from urllib.parse import urlparse

    from urllib.parse import unquote

    parsed = urlparse(url)
    if parsed.scheme == "file":
        # ファイルマネージャからのD&Dは file:/// のURLだけ渡ることがある
        path = unquote(parsed.path)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail=f"ファイルが見つかりません: {path}")
        try:
            with Image.open(path) as im:
                fmt = (im.format or "").lower()
        except Exception:
            raise HTTPException(status_code=415, detail="画像ではありません")
        with open(path, "rb") as f:
            return _image_response(f.read(), f"image/{'jpeg' if fmt == 'jpeg' else fmt or 'png'}")
    if parsed.scheme not in ("http", "https") or parsed.hostname not in (
        "localhost", "127.0.0.1", "::1",
    ):
        raise HTTPException(status_code=400, detail="ローカルのURLのみ取得できます")
    import requests
    from starlette.concurrency import run_in_threadpool

    try:
        resp = await run_in_threadpool(requests.get, url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"画像の取得に失敗しました: {e}")
    ctype = resp.headers.get("content-type", "")
    if not ctype.startswith("image/"):
        raise HTTPException(status_code=415, detail="画像ではありません")
    return _image_response(resp.content, ctype)


@app.get("/api/meta")
async def meta():
    return {
        "views": [{"key": k, "label": l} for k, l in VIEW_LABELS],
        "variants": [{"key": k, "label": l} for k, l in VARIANT_LABELS],
        "modes": [{"key": k, "label": l} for k, l in MODE_LABELS],
        "sizes": [{"key": k, "label": l} for k, l in SIZE_LABELS],
        "defaults": {
            "partial_target": DEFAULT_PARTIAL_TARGET,
            "partial_color": DEFAULT_PARTIAL_COLOR,
            "mode": "illustration",
            "sizes": ["small", "medium", "large"],
        },
        "image_server_url": config.IMAGE_SERVER_URL,
    }


@app.get("/api/health")
async def health():
    try:
        st = client.server_status()
        return {
            "ok": True,
            "image_server_url": config.IMAGE_SERVER_URL,
            "gpu_busy": st.get("gpu_busy"),
            "vram_free_gb": (st.get("vram") or {}).get("free_gb"),
        }
    except client.ImageServerError as exc:
        return {"ok": False, "image_server_url": config.IMAGE_SERVER_URL, "error": str(exc)}


@app.post("/api/sheet/generate")
async def generate(
    image: UploadFile = File(...),
    seed: int = Form(-1),
    views: str = Form("front,back,left,right,front_left_45,front_right_45"),
    variants: str = Form("color,lineart,partial"),
    stylize: bool = Form(True),
    partial_target: str = Form(DEFAULT_PARTIAL_TARGET),
    partial_color: str = Form(DEFAULT_PARTIAL_COLOR),
    quant: str = Form(""),
    lightning: bool = Form(True),
    layout: str = Form("a4"),
    hero_variant: str = Form("lineart"),
    hero_view: str = Form("front"),
    mode: str = Form(""),
    sizes: str = Form("small,medium,large"),
):
    if layout not in ("a4", "grid"):
        raise HTTPException(status_code=400, detail="layout は a4 / grid のいずれかを指定してください。")
    mode = mode.strip().lower()
    mix = mode == "mix"
    if mix:
        mode = ""
    size_list = list(dict.fromkeys(s.strip().lower() for s in sizes.split(",") if s.strip()))
    if mode:
        if mode not in MODE_KEYS:
            raise HTTPException(status_code=400, detail=f"mode は {MODE_KEYS} から選択してください。")
        bad_sizes = [s for s in size_list if s not in SIZE_KEYS]
        if bad_sizes or not size_list:
            raise HTTPException(status_code=400, detail=f"sizes は {SIZE_KEYS} から1つ以上選択してください。")

    hero_variant = hero_variant.strip().lower()
    hero_view = hero_view.strip().lower()
    hero_keys = [k for k, _ in VARIANT_LABELS] + MODE_KEYS
    if hero_variant in ("", "none"):
        hero_variant = None
    elif hero_variant not in hero_keys:
        raise HTTPException(status_code=400, detail=f"hero_variant は none または {hero_keys} から指定してください。")
    if hero_variant and hero_view not in [k for k, _ in VIEW_LABELS]:
        raise HTTPException(status_code=400, detail="hero_view が不正です。")
    view_list = [v.strip() for v in views.split(",") if v.strip()]
    variant_list = [v.strip() for v in variants.split(",") if v.strip()]
    try:
        if mix:
            bad = [v for v in variant_list if v not in MODE_KEYS]
            if bad or not variant_list:
                raise ValueError(f"MIXする表現は {MODE_KEYS} から1つ以上選択してください: {bad}")
            bad_views = [v for v in view_list if v not in jobs.VIEW_KEYS]
            if bad_views or not view_list:
                raise ValueError(f"ビューを1つ以上正しく選択してください: {bad_views}")
        elif mode:
            jobs.validate_mode_params(view_list, mode, size_list)
        else:
            jobs.validate_params(view_list, variant_list)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    contents = await image.read()
    try:
        # EXIF回転を実ピクセルへ適用してからPNG化(スマホ撮影画像対策)
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(contents)))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        image_bytes = buf.getvalue()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"画像の読み込みに失敗しました: {exc}")

    if seed is None or seed < 0:
        seed = int.from_bytes(os.urandom(4), "little") % (2**31)

    params = {
        "seed": seed,
        "views": view_list,
        "variants": variant_list,
        "stylize": stylize,
        "partial_target": partial_target,
        "partial_color": partial_color,
        "quant": quant.strip(),
        "lightning": lightning,
        "layout": layout,
        "hero_variant": hero_variant,
        "hero_view": hero_view,
        "mode": mode or None,
        "mix": mix,
        "sizes": size_list,
    }
    try:
        job_id = jobs.start_job(image_bytes, params)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"job_id": job_id, "seed": seed}


@app.post("/api/sheet/jobs/{job_id}/recompose")
async def recompose(
    job_id: str,
    views: str = Form(""),
    variants: str = Form(""),
    layout: str = Form(""),
    hero_variant: str = Form(""),
    hero_view: str = Form(""),
    sizes: str = Form(""),
):
    """生成済み画像からシートを再合成する(生成なし・数秒)。空の項目は元ジョブの設定を使う。"""
    _check_job_id(job_id)
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    p = job.get("params") or {}

    view_list = [v.strip() for v in views.split(",") if v.strip()] or p.get("views", [])
    variant_list = [v.strip() for v in variants.split(",") if v.strip()] or p.get("variants", [])
    layout = (layout.strip() or p.get("layout", "a4"))
    if layout not in ("a4", "grid"):
        raise HTTPException(status_code=400, detail="layout は a4 / grid のいずれかを指定してください。")
    hv = hero_variant.strip().lower()
    if hv == "":
        hv = p.get("hero_variant")
    elif hv == "none":
        hv = None
    elif hv not in [k for k, _ in VARIANT_LABELS] + MODE_KEYS:
        raise HTTPException(status_code=400, detail="hero_variant が不正です。")
    hview = hero_view.strip().lower() or p.get("hero_view") or "front"
    if hv and hview not in [k for k, _ in VIEW_LABELS]:
        raise HTTPException(status_code=400, detail="hero_view が不正です。")

    size_list = list(dict.fromkeys(s.strip().lower() for s in sizes.split(",") if s.strip()))
    if not size_list:
        size_list = p.get("sizes") or ["small", "medium", "large"]
    bad_sizes = [s for s in size_list if s not in SIZE_KEYS]
    if bad_sizes:
        raise HTTPException(status_code=400, detail=f"sizes に不正な値があります: {bad_sizes}")

    try:
        result = jobs.recompose_job(job_id, view_list, variant_list, layout, hview, hv,
                                    sizes=size_list)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return {"ok": True, **result}


@app.get("/api/sheet/jobs")
async def job_list():
    return {"jobs": jobs.list_jobs()}


@app.get("/api/sheet/jobs/{job_id}")
async def job_status(job_id: str):
    _check_job_id(job_id)
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return job


def _job_file(job_id: str, filename: str) -> str:
    _check_job_id(job_id)
    path = os.path.join(config.OUTPUTS_DIR, job_id, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="ファイルが見つかりません")
    return path


@app.get("/api/sheet/jobs/{job_id}/images/{name}.png")
async def job_image(job_id: str, name: str):
    if not _SAFE_IMAGE.match(name):
        raise HTTPException(status_code=404, detail="不正なファイル名です")
    return FileResponse(_job_file(job_id, f"{name}.png"), media_type="image/png",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/sheet/jobs/{job_id}/sheet.png")
async def job_sheet(job_id: str):
    return FileResponse(_job_file(job_id, "sheet.png"), media_type="image/png",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/sheet/jobs/{job_id}/download.zip")
async def job_zip(job_id: str):
    return FileResponse(
        _job_file(job_id, "download.zip"), media_type="application/zip",
        filename=f"design_sheet_{job_id}.zip",
        headers={"Cache-Control": "no-store"},
    )


app.mount("/", StaticFiles(directory=os.path.join(config.PROJECT_ROOT, "static"), html=True),
          name="static")
