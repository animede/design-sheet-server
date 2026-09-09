# -*- coding: utf-8 -*-
"""diffusers-image-server への HTTP クライアント。

GPU処理はすべてここ経由でバックエンドに委譲する。接続先は
`DS_SHEET_IMAGE_SERVER_URL`(core/config.py)。バックエンドが 409
(GPUビジー/別ジョブ実行中)を返した場合は一定回数リトライする。
"""
import io
import time

import requests

from core import config


class ImageServerError(RuntimeError):
    """バックエンド呼び出しの失敗(接続不可・4xx/5xx)。メッセージはそのままUIに出す。"""


def _detail(resp: requests.Response) -> str:
    try:
        return resp.json().get("detail", resp.text[:300])
    except Exception:
        return resp.text[:300]


def _post_with_busy_retry(url: str, *, files=None, data=None, timeout=None):
    """409(ビジー)をリトライしつつPOSTする。それ以外のエラーは即例外。"""
    last = None
    for _ in range(config.BUSY_RETRIES + 1):
        try:
            resp = requests.post(url, files=files, data=data, timeout=timeout)
        except requests.RequestException as exc:
            raise ImageServerError(
                f"画像サーバ({config.IMAGE_SERVER_URL})に接続できません: {exc}"
            ) from exc
        if resp.status_code != 409:
            return resp
        last = resp
        time.sleep(config.BUSY_RETRY_INTERVAL_S)
    raise ImageServerError(
        f"画像サーバがビジーのままリトライ上限に達しました: {_detail(last)}"
    )


def fetch_bytes(path_or_url: str) -> bytes:
    """バックエンドの相対URL(/outputs/... 等)から画像バイトを取得する。"""
    url = path_or_url if path_or_url.startswith("http") else config.IMAGE_SERVER_URL + path_or_url
    try:
        resp = requests.get(url, timeout=60)
    except requests.RequestException as exc:
        raise ImageServerError(f"画像の取得に失敗しました: {exc}") from exc
    if resp.status_code != 200:
        raise ImageServerError(f"画像の取得に失敗しました(HTTP {resp.status_code}): {url}")
    return resp.content


def server_status() -> dict:
    try:
        # GPUが他ジョブで飽和しているとVRAM照会が遅れるため長めに待つ
        resp = requests.get(config.IMAGE_SERVER_URL + "/api/status", timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise ImageServerError(
            f"画像サーバ({config.IMAGE_SERVER_URL})に接続できません: {exc}"
        ) from exc


def edit(
    image_bytes: bytes,
    prompt: str,
    seed: int,
    width: int = None,
    height: int = None,
    quant: str = None,
    lightning: bool = True,
) -> bytes:
    """/api/edit を1回呼んで結果PNGのバイトを返す。"""
    data = {
        "prompt": prompt,
        "seed": str(seed),
        "lightning": "true" if lightning else "false",
    }
    if width:
        data["width"] = str(int(width))
    if height:
        data["height"] = str(int(height))
    if quant:
        data["quant"] = quant
    files = {"images": ("input.png", io.BytesIO(image_bytes), "image/png")}
    resp = _post_with_busy_retry(
        config.IMAGE_SERVER_URL + "/api/edit",
        files=files,
        data=data,
        timeout=config.EDIT_TIMEOUT_S,
    )
    if resp.status_code != 200:
        raise ImageServerError(f"/api/edit が失敗しました(HTTP {resp.status_code}): {_detail(resp)}")
    body = resp.json()
    image_url = body.get("image_url")
    if not image_url:
        raise ImageServerError(f"/api/edit の応答に image_url がありません: {body}")
    return fetch_bytes(image_url)


def charsheet_generate(image_bytes: bytes, seed: int) -> str:
    """多視点(8方向)ジョブを開始して job_id を返す。"""
    files = {"image": ("input.png", io.BytesIO(image_bytes), "image/png")}
    resp = _post_with_busy_retry(
        config.IMAGE_SERVER_URL + "/api/charsheet/generate",
        files=files,
        data={"seed": str(seed)},
        timeout=60,
    )
    if resp.status_code != 200:
        raise ImageServerError(
            f"/api/charsheet/generate が失敗しました(HTTP {resp.status_code}): {_detail(resp)}"
        )
    return resp.json()["job_id"]


def charsheet_status(job_id: str) -> dict:
    try:
        resp = requests.get(
            config.IMAGE_SERVER_URL + f"/api/charsheet/jobs/{job_id}", timeout=30
        )
    except requests.RequestException as exc:
        raise ImageServerError(f"charsheet ジョブの状態取得に失敗しました: {exc}") from exc
    if resp.status_code != 200:
        raise ImageServerError(
            f"charsheet ジョブの状態取得に失敗しました(HTTP {resp.status_code}): {_detail(resp)}"
        )
    return resp.json()


def charsheet_wait(job_id: str, on_progress=None) -> dict:
    """charsheet ジョブの完了(またはエラー)まで待つ。on_progress(status_dict) を毎回呼ぶ。"""
    while True:
        status = charsheet_status(job_id)
        if on_progress is not None:
            on_progress(status)
        st = status.get("status")
        if st in ("done", "error"):
            return status
        time.sleep(config.POLL_INTERVAL_S)


def charsheet_view_image(job_id: str, key: str) -> bytes:
    return fetch_bytes(f"/api/charsheet/jobs/{job_id}/images/{key}.png")
