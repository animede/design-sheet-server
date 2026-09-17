# -*- coding: utf-8 -*-
"""シート生成ジョブ(スレッド実行、同時1件)。

現行の表現モード版パイプライン:
  1. styling: 入力 → リアル/アニメ/イラスト/ちび等の基準画像(/api/edit)
  2. views: charsheetで多視点を生成
  3. finishing: 線画・部分彩色のみ各ビューを仕上げる
  4. compose: 選択した小/中/大の表示倍率でシートを合成

旧API互換パイプライン(mode未指定時):
  1. stylize(任意): 入力 → フラット彩色イラスト化(/api/edit)
  2. views: charsheet ジョブ(8方向、angles adapter)→ 選択ビューの画像を取得
  3. variants: 各ビューに 線画(/api/edit)、部分彩色(線画への2パス目 /api/edit)
  4. compose: PILでグリッド合成(sheet.png)+ download.zip

セル単位の失敗はジョブ全体を止めず cell_errors に記録して続行する。
"""
import io
import json
import os
import threading
import time
import uuid
import zipfile

from PIL import Image

from core import client, config, sheet
from core.prompts import (
    LINEART_PROMPT,
    MODE_KEYS,
    SIZE_KEYS,
    STYLIZE_PROMPT,
    VARIANT_KEYS,
    VIEW_KEYS,
    build_mode_prompt,
    build_partial_prompt,
)

_jobs = {}
_jobs_lock = threading.Lock()
_current_job_id = None


def _job_dir(job_id: str) -> str:
    return os.path.join(config.OUTPUTS_DIR, job_id)


def _persist(job: dict):
    path = os.path.join(_job_dir(job["job_id"]), "job.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=1)


def _update(job_id: str, **kwargs):
    with _jobs_lock:
        job = _jobs[job_id]
        job.update(kwargs)
        _persist(job)


def _set_cell(job_id: str, view: str, variant: str, status: str):
    with _jobs_lock:
        job = _jobs[job_id]
        job["views"][view][variant] = status
        _persist(job)


def list_jobs(limit: int = 20):
    """outputs/ にある過去ジョブの一覧(新しい順)。再起動後の履歴表示用。"""
    entries = []
    if not os.path.isdir(config.OUTPUTS_DIR):
        return entries
    for name in os.listdir(config.OUTPUTS_DIR):
        path = os.path.join(config.OUTPUTS_DIR, name, "job.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                job = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        entries.append({
            "job_id": job.get("job_id", name),
            "status": job.get("status"),
            "sheet_ready": bool(job.get("sheet_ready")),
            "mode": (job.get("params") or {}).get("mode"),
            "started_at": job.get("started_at"),
            "mtime": os.path.getmtime(path),
        })
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries[:limit]


def get_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            return dict(job)
    # プロセス再起動後はディスクから復元(読み取り専用)
    path = os.path.join(_job_dir(job_id), "job.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _round32(v: int) -> int:
    return max(256, (int(v) // 32) * 32)


def _edit_dims_for(img_bytes: bytes):
    """EDIT_SIZE 指定時のみ明示解像度を返す(未指定はバックエンドの自動推定に任せる)。"""
    if config.EDIT_SIZE <= 0:
        return None, None
    with Image.open(io.BytesIO(img_bytes)) as im:
        w, h = im.size
    area = config.EDIT_SIZE * config.EDIT_SIZE
    scale = (area / (w * h)) ** 0.5
    return _round32(w * scale), _round32(h * scale)


def start_job(image_bytes: bytes, params: dict):
    """ジョブを開始して job_id を返す。実行中ジョブがあれば ValueError。"""
    global _current_job_id
    with _jobs_lock:
        if _current_job_id is not None:
            raise ValueError("別のシート生成ジョブが実行中です。完了を待ってから再試行してください。")
        job_id = uuid.uuid4().hex[:12]
        _current_job_id = job_id

    job_dir = _job_dir(job_id)
    os.makedirs(job_dir, exist_ok=True)
    with open(os.path.join(job_dir, "input.png"), "wb") as f:
        f.write(image_bytes)

    views = params["views"]
    variants = [params["mode"]] if params.get("mode") else params["variants"]
    job = {
        "job_id": job_id,
        "status": "queued",
        "error": None,
        "cell_errors": {},
        "params": params,
        "views": {v: {var: "pending" for var in variants} for v in views},
        "charsheet": None,
        "sheet_ready": False,
        "stylized": False,
        "started_at": time.time(),
        "finished_at": None,
    }
    with _jobs_lock:
        _jobs[job_id] = job
        _persist(job)

    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()
    return job_id


def _generate_mode_views(job_id: str, job_dir: str, params: dict, mode: str):
    """1つの表現モードの styling→views→finishing を実行し {view}_{mode}.png を作る。"""
    views = params["views"]
    seed = params["seed"]
    quant = params.get("quant") or config.DEFAULT_QUANT or None
    lightning = params.get("lightning", True)

    with open(os.path.join(job_dir, "input.png"), "rb") as f:
        base_bytes = f.read()

    # 1. 選択した表現へ基準画像を正規化する。
    _update(job_id, status="styling")
    w, h = _edit_dims_for(base_bytes)
    base_bytes = client.edit(
        base_bytes, build_mode_prompt(mode), seed, width=w, height=h,
        quant=quant, lightning=lightning,
    )
    with open(os.path.join(job_dir, f"prepared_{mode}.png"), "wb") as f:
        f.write(base_bytes)
    _update(job_id, stylized=True)

    # 2. 基準画像から多視点を生成する。
    _update(job_id, status="views")
    cs_job_id = client.charsheet_generate(base_bytes, seed)

    def on_progress(st):
        _update(job_id, charsheet={
            "job_id": cs_job_id,
            "status": st.get("status"),
            "progress": st.get("progress"),
            "total": st.get("total"),
        })

    cs_status = client.charsheet_wait(cs_job_id, on_progress=on_progress)
    if cs_status.get("status") == "error":
        raise client.ImageServerError(
            f"多視点生成(charsheet)が失敗しました: {cs_status.get('error')}"
        )

    sources = {}
    for view in views:
        source = client.charsheet_view_image(cs_job_id, view)
        sources[view] = source
        with open(os.path.join(job_dir, f"{view}_source.png"), "wb") as f:
            f.write(source)
        if mode not in ("lineart", "partial"):
            with open(os.path.join(job_dir, f"{view}_{mode}.png"), "wb") as f:
                f.write(source)
            _set_cell(job_id, view, mode, "done")

    # 3. 線画系だけは各ビューを後段で仕上げ、線の品質を揃える。
    if mode in ("lineart", "partial"):
        _update(job_id, status="finishing")
        partial_prompt = build_partial_prompt(
            params.get("partial_target"), params.get("partial_color")
        )
        for view, source in sources.items():
            try:
                _set_cell(job_id, view, mode, "running")
                w, h = _edit_dims_for(source)
                lineart = client.edit(
                    source, LINEART_PROMPT, seed, width=w, height=h,
                    quant=quant, lightning=lightning,
                )
                with open(os.path.join(job_dir, f"{view}_lineart.png"), "wb") as f:
                    f.write(lineart)
                final = lineart
                if mode == "partial":
                    final = client.edit(
                        lineart, partial_prompt, seed, width=w, height=h,
                        quant=quant, lightning=lightning,
                    )
                    with open(os.path.join(job_dir, f"{view}_partial.png"), "wb") as f:
                        f.write(final)
                _set_cell(job_id, view, mode, "done")
            except client.ImageServerError as exc:
                _set_cell(job_id, view, mode, "error")
                _record_cell_error(job_id, f"{view}/{mode}", str(exc))


def _run_mode_steps(job_id: str, job_dir: str, params: dict):
    """表現モード版パイプライン。例外処理と実行枠の解放は _run_job が担う。"""
    mode = params["mode"]
    sizes = params.get("sizes") or ["small", "medium", "large"]
    _generate_mode_views(job_id, job_dir, params, mode)
    _update(job_id, status="composing")
    title = (f"Character Design Sheet — seed={params['seed']}  "
             f"({time.strftime('%Y-%m-%d %H:%M')})")
    sheet.compose_mode_sheet(
        job_dir, title, params["views"], mode, sizes, layout=params.get("layout", "a4")
    )
    _build_zip(job_dir)
    _update(job_id, status="done", sheet_ready=True, finished_at=time.time())


def _run_mix_steps(job_id: str, job_dir: str, params: dict):
    """MIX版パイプライン: 選択された複数の表現モードを順に生成し、
    「左=入力+主役大判、右=多視点×表現」のシートに合成する。"""
    modes = params["variants"]
    for m in modes:
        try:
            _generate_mode_views(job_id, job_dir, params, m)
        except client.ImageServerError as exc:
            # この表現の生成失敗は記録して次の表現へ進む
            for v in params["views"]:
                _set_cell(job_id, v, m, "error")
            _record_cell_error(job_id, f"*/{m}", str(exc))
    _update(job_id, status="composing")
    title = (f"Character Design Sheet — seed={params['seed']}  "
             f"({time.strftime('%Y-%m-%d %H:%M')})")
    sheet.compose_sheet(job_dir, title, params["views"], modes, include_input=True,
                        layout=params.get("layout", "a4"),
                        hero_view=params.get("hero_view"),
                        hero_variant=params.get("hero_variant"))
    _build_zip(job_dir)
    _update(job_id, status="done", sheet_ready=True, finished_at=time.time())


def _run_job(job_id: str):
    global _current_job_id
    job_dir = _job_dir(job_id)
    params = get_job(job_id)["params"]
    views = params["views"]
    variants = params["variants"]
    seed = params["seed"]
    quant = params.get("quant") or config.DEFAULT_QUANT or None
    lightning = params.get("lightning", True)

    try:
        if params.get("mix"):
            _run_mix_steps(job_id, job_dir, params)
            return
        if params.get("mode"):
            _run_mode_steps(job_id, job_dir, params)
            return

        with open(os.path.join(job_dir, "input.png"), "rb") as f:
            base_bytes = f.read()

        # --- 1. stylize(任意) ---
        if params.get("stylize", True):
            _update(job_id, status="stylizing")
            w, h = _edit_dims_for(base_bytes)
            base_bytes = client.edit(
                base_bytes, STYLIZE_PROMPT, seed, width=w, height=h,
                quant=quant, lightning=lightning,
            )
            with open(os.path.join(job_dir, "stylized.png"), "wb") as f:
                f.write(base_bytes)
            _update(job_id, stylized=True)

        # --- 2. views(charsheet 8方向ジョブ。選択ビューのみ取得) ---
        _update(job_id, status="views")
        cs_job_id = client.charsheet_generate(base_bytes, seed)

        def on_progress(st):
            _update(job_id, charsheet={
                "job_id": cs_job_id,
                "status": st.get("status"),
                "progress": st.get("progress"),
                "total": st.get("total"),
            })

        cs_status = client.charsheet_wait(cs_job_id, on_progress=on_progress)
        if cs_status.get("status") == "error":
            raise client.ImageServerError(
                f"多視点生成(charsheet)が失敗しました: {cs_status.get('error')}"
            )
        for v in views:
            img = client.charsheet_view_image(cs_job_id, v)
            with open(os.path.join(job_dir, f"{v}_color.png"), "wb") as f:
                f.write(img)
            if "color" in variants:
                _set_cell(job_id, v, "color", "done")

        # --- 3. variants(線画 → 部分彩色の2パス) ---
        _update(job_id, status="variants")
        partial_prompt = build_partial_prompt(
            params.get("partial_target"), params.get("partial_color")
        )
        for v in views:
            with open(os.path.join(job_dir, f"{v}_color.png"), "rb") as f:
                color_bytes = f.read()
            w, h = _edit_dims_for(color_bytes)

            lineart_bytes = None
            if "lineart" in variants or "partial" in variants:
                try:
                    if "lineart" in variants:
                        _set_cell(job_id, v, "lineart", "running")
                    lineart_bytes = client.edit(
                        color_bytes, LINEART_PROMPT, seed, width=w, height=h,
                        quant=quant, lightning=lightning,
                    )
                    with open(os.path.join(job_dir, f"{v}_lineart.png"), "wb") as f:
                        f.write(lineart_bytes)
                    if "lineart" in variants:
                        _set_cell(job_id, v, "lineart", "done")
                except client.ImageServerError as exc:
                    if "lineart" in variants:
                        _set_cell(job_id, v, "lineart", "error")
                    _record_cell_error(job_id, f"{v}/lineart", str(exc))

            if "partial" in variants:
                if lineart_bytes is None:
                    _set_cell(job_id, v, "partial", "error")
                    _record_cell_error(job_id, f"{v}/partial", "線画の生成に失敗したためスキップ")
                else:
                    try:
                        _set_cell(job_id, v, "partial", "running")
                        partial_bytes = client.edit(
                            lineart_bytes, partial_prompt, seed, width=w, height=h,
                            quant=quant, lightning=lightning,
                        )
                        with open(os.path.join(job_dir, f"{v}_partial.png"), "wb") as f:
                            f.write(partial_bytes)
                        _set_cell(job_id, v, "partial", "done")
                    except client.ImageServerError as exc:
                        _set_cell(job_id, v, "partial", "error")
                        _record_cell_error(job_id, f"{v}/partial", str(exc))

        # --- 4. compose ---
        _update(job_id, status="composing")
        title = f"Character Design Sheet — seed={seed}  ({time.strftime('%Y-%m-%d %H:%M')})"
        sheet.compose_sheet(job_dir, title, views, variants, include_input=True,
                            layout=params.get("layout", "a4"),
                            hero_view=params.get("hero_view"),
                            hero_variant=params.get("hero_variant"))
        _build_zip(job_dir)
        _update(job_id, status="done", sheet_ready=True, finished_at=time.time())
    except Exception as exc:  # ステージレベルの失敗はジョブ全体をエラーにする
        _update(job_id, status="error", error=str(exc), finished_at=time.time())
    finally:
        with _jobs_lock:
            _current_job_id = None


def recompose_job(job_id: str, views, variants, layout, hero_view, hero_variant, sizes=None):
    """生成済み画像から sheet.png / download.zip を作り直す(GPU不要・即時)。

    ジョブで生成していないビューは自動的に除外して skipped として返す。
    """
    job = get_job(job_id)
    if job is None:
        return None
    if job.get("status") not in ("done", "error"):
        raise ValueError("ジョブ実行中は再配置できません。完了後に実行してください。")
    job_dir = _job_dir(job_id)
    if not os.path.isdir(job_dir):
        raise ValueError("ジョブの画像フォルダが見つかりません。")

    mode = (job.get("params") or {}).get("mode")
    if mode:
        bad_views = [v for v in views if v not in VIEW_KEYS]
        if bad_views or not views:
            raise ValueError(f"ビュー指定が不正です: {bad_views}")
        sizes = sizes or (job.get("params") or {}).get("sizes") or ["large"]
        bad_sizes = [s for s in sizes if s not in SIZE_KEYS]
        if bad_sizes or not sizes:
            raise ValueError(f"サイズ指定が不正です: {bad_sizes}")
        avail = [v for v in views
                 if os.path.exists(os.path.join(job_dir, f"{v}_{mode}.png"))]
        skipped = [v for v in views if v not in avail]
        if not avail:
            raise ValueError("指定ビューの生成済み画像がありません。")
        seed = (job.get("params") or {}).get("seed", "-")
        title = f"Character Design Sheet — seed={seed}  ({time.strftime('%Y-%m-%d %H:%M')})"
        sheet.compose_mode_sheet(job_dir, title, avail, mode, sizes, layout=layout)
        _build_zip(job_dir)
        with _jobs_lock:
            target = _jobs.get(job_id) or job
            target["sheet_ready"] = True
            target["sheet_rev"] = int(target.get("sheet_rev", 0)) + 1
            target["recompose"] = {
                "views": avail, "mode": mode, "sizes": sizes, "layout": layout,
            }
            with open(os.path.join(job_dir, "job.json"), "w", encoding="utf-8") as f:
                json.dump(target, f, ensure_ascii=False, indent=1)
            return {"sheet_rev": target["sheet_rev"], "views": avail,
                    "skipped_views": skipped}

    if (job.get("params") or {}).get("mix"):
        bad = [v for v in variants if v not in MODE_KEYS]
        if bad or not variants:
            raise ValueError(f"未知の表現です: {bad}(有効: {MODE_KEYS})")
        bad_views = [v for v in views if v not in VIEW_KEYS]
        if bad_views or not views:
            raise ValueError(f"ビュー指定が不正です: {bad_views}")
        check = list(variants)
    else:
        validate_params(views, variants)
        check = list(dict.fromkeys(list(variants) + ["color"]))
    avail = [v for v in views
             if any(os.path.exists(os.path.join(job_dir, f"{v}_{var}.png")) for var in check)]
    skipped = [v for v in views if v not in avail]
    if not avail:
        raise ValueError("指定ビューの生成済み画像がありません(このジョブで生成したビューから選んでください)。")

    seed = (job.get("params") or {}).get("seed", "-")
    title = f"Character Design Sheet — seed={seed}  ({time.strftime('%Y-%m-%d %H:%M')})"
    sheet.compose_sheet(job_dir, title, avail, variants, include_input=True,
                        layout=layout, hero_view=hero_view, hero_variant=hero_variant)
    _build_zip(job_dir)

    with _jobs_lock:
        target = _jobs.get(job_id) or job
        target["sheet_ready"] = True
        target["sheet_rev"] = int(target.get("sheet_rev", 0)) + 1
        target["recompose"] = {
            "views": avail, "variants": variants, "layout": layout,
            "hero_view": hero_view, "hero_variant": hero_variant,
        }
        with open(os.path.join(job_dir, "job.json"), "w", encoding="utf-8") as f:
            json.dump(target, f, ensure_ascii=False, indent=1)
        return {"sheet_rev": target["sheet_rev"], "views": avail, "skipped_views": skipped}


def _record_cell_error(job_id: str, cell: str, message: str):
    with _jobs_lock:
        job = _jobs[job_id]
        job["cell_errors"][cell] = message
        _persist(job)


def _build_zip(job_dir: str):
    zip_path = os.path.join(job_dir, "download.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(job_dir)):
            if name.endswith(".png"):
                z.write(os.path.join(job_dir, name), name)


def validate_params(views, variants):
    bad = [v for v in views if v not in VIEW_KEYS]
    if bad:
        raise ValueError(f"未知のビューです: {bad}(有効: {VIEW_KEYS})")
    bad = [v for v in variants if v not in VARIANT_KEYS]
    if bad:
        raise ValueError(f"未知の変種です: {bad}(有効: {VARIANT_KEYS})")
    if not views:
        raise ValueError("ビューを1つ以上選択してください。")
    if not variants:
        raise ValueError("変種を1つ以上選択してください。")


def validate_mode_params(views, mode, sizes):
    bad_views = [v for v in views if v not in VIEW_KEYS]
    if bad_views or not views:
        raise ValueError(f"ビューを1つ以上正しく選択してください: {bad_views}")
    if mode not in MODE_KEYS:
        raise ValueError(f"未知の表現モードです: {mode}")
    bad_sizes = [s for s in sizes if s not in SIZE_KEYS]
    if bad_sizes or not sizes:
        raise ValueError(f"サイズを1つ以上正しく選択してください: {bad_sizes}")
