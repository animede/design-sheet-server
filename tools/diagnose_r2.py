# -*- coding: utf-8 -*-
"""R2 の認証情報が「何をできて何をできないか」を切り分ける。

AccessDenied は「トークンの権限不足」「バケット名の誤り」「トークンのスコープ外」の
どれでも出る。読み・一覧・書きを個別に試して、どれが通るかを表示する。

    ./venv/bin/python tools/diagnose_r2.py

認証情報そのものは一切表示しない。
"""
import os
import sys

import boto3
from boto3.exceptions import Boto3Error
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

# tools/ から実行されるため、リポジトリ直下を import パスへ追加する。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import cloud_share, config  # noqa: E402

TEST_KEY = "design-sheets/.permission-probe"


def attempt(label: str, fn):
    try:
        result = fn()
    except (BotoCoreError, Boto3Error, ClientError, OSError) as exc:
        print(f"{label:24} NG   {cloud_share._error_code(exc)}")
        return None
    print(f"{label:24} OK   {result if result is not None else ''}")
    return result


def main() -> int:
    print(f"endpoint : {config.R2_ENDPOINT}")
    print(f"bucket   : {config.R2_BUCKET}")
    print(f"有効判定 : {config.R2_SHARE_ENABLED}"
          f"{' / ' + config.R2_ENDPOINT_PROBLEM if config.R2_ENDPOINT_PROBLEM else ''}")
    print()

    client = boto3.client(
        "s3",
        endpoint_url=config.R2_ENDPOINT,
        aws_access_key_id=config.R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )

    attempt("バケット一覧(list)", lambda: [
        b["Name"] for b in client.list_buckets().get("Buckets", [])])
    attempt("バケット参照(head)",
            lambda: client.head_bucket(Bucket=config.R2_BUCKET) and "アクセス可")
    attempt("オブジェクト一覧(list)", lambda: len(client.list_objects_v2(
        Bucket=config.R2_BUCKET, MaxKeys=1).get("Contents", [])))
    wrote = attempt("書き込み(put)", lambda: client.put_object(
        Bucket=config.R2_BUCKET, Key=TEST_KEY, Body=b"probe") and "1バイト書けた")
    if wrote is not None:
        attempt("削除(delete)", lambda: client.delete_object(
            Bucket=config.R2_BUCKET, Key=TEST_KEY) and "後片付け完了")

    print()
    print("※ バケット一覧(list)の AccessDenied は、バケット限定トークンでは正常")
    print("書き込み(put)がNG → トークンを Object Read & Write で作り直す")
    print("参照(head)もNG    → バケット名の誤りか、トークンのスコープ外")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
