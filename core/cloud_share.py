# -*- coding: utf-8 -*-
"""Cloudflare R2 へ生成シートを送り、期限付きURLとQR画像を作る。"""
import base64
import io
import os
import re

import boto3
import qrcode
from boto3.exceptions import Boto3Error
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from core import config


class CloudShareError(RuntimeError):
    """クラウド共有処理で利用者へ表示できるエラー。"""


def _error_code(exc: Exception) -> str:
    """S3のエラーコード(AccessDenied 等)を取り出す。認証情報は含まれない。

    upload_file は高水準APIなので ClientError を S3UploadFailedError で包む。
    その場合 response 属性が無く、コードはメッセージの中にしか無い。
    """
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if code:
            return str(code)
    m = re.search(r"An error occurred \(([A-Za-z0-9]+)\)", str(exc))
    return m.group(1) if m else type(exc).__name__


def is_enabled() -> bool:
    return config.R2_SHARE_ENABLED


def _client():
    try:
        return boto3.client(
            "s3",
            endpoint_url=config.R2_ENDPOINT,
            aws_access_key_id=config.R2_ACCESS_KEY_ID,
            aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )
    except (BotoCoreError, ClientError, ValueError) as exc:
        # エンドポイントURLが不正だと botocore は ValueError を投げる。捕まえないと
        # 設定ミスがそのまま 500 になり、UIには理由が出ない。
        raise CloudShareError(f"R2の接続設定が不正です ({type(exc).__name__})") from exc


def _qr_data_url(url: str) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def share_sheet(job_id: str, sheet_path: str) -> dict:
    """sheet.png をR2へ保存し、設定された期間だけ有効なGET URLを返す。"""
    if not is_enabled():
        raise CloudShareError("Cloudflare R2が設定されていません")
    if not os.path.isfile(sheet_path):
        raise CloudShareError("共有するシートが見つかりません")

    key = f"design-sheets/{job_id}/sheet.png"
    filename = f"design_sheet_{job_id}.png"
    client = _client()
    try:
        client.upload_file(
            sheet_path,
            config.R2_BUCKET,
            key,
            ExtraArgs={
                "ContentType": "image/png",
                "CacheControl": "private, no-store",
            },
        )
        url = client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": config.R2_BUCKET,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
                "ResponseContentType": "image/png",
            },
            ExpiresIn=config.R2_URL_TTL_S,
        )
    # upload_file は ClientError を boto3.exceptions.S3UploadFailedError で包むため、
    # botocore 側の例外だけを捕まえると 500 になる(実際に踏んだ)。Boto3Error も見る。
    except (BotoCoreError, Boto3Error, ClientError, OSError) as exc:
        # 応答本文や署名URLをそのままUIへ返さず、認証情報漏えいを避ける。
        # エラーコード(AccessDenied 等)だけは原因究明に要るので残す。
        code = _error_code(exc)
        hint = {
            "AccessDenied": " — APIトークンに対象バケットの Object Read & Write 権限がありません",
            "NoSuchBucket": f" — バケット {config.R2_BUCKET} が存在しません",
            "InvalidAccessKeyId": " — Access Key ID が誤っています",
            "SignatureDoesNotMatch": " — Secret Access Key が誤っています",
        }.get(code, "")
        raise CloudShareError(f"R2へのアップロードに失敗しました ({code}){hint}") from exc

    try:
        qr_data_url = _qr_data_url(url)
    except (ValueError, qrcode.exceptions.DataOverflowError) as exc:
        raise CloudShareError("共有URLが長すぎるためQRコードを作成できません") from exc

    return {
        "url": url,
        "qr_data_url": qr_data_url,
        "expires_in": config.R2_URL_TTL_S,
    }
