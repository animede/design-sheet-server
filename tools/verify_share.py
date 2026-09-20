# -*- coding: utf-8 -*-
"""Cloudflare R2 共有の疎通を実アップロードで検証する。

`/api/meta` の `cloud_share.enabled` は4項目が空でないことしか見ていないため、
鍵が実際にR2で通るかはアップロードしないと分からない。

署名URLには `X-Amz-Credential`(アクセスキーID)が含まれるので、URL自体は表示せず
ホスト・キー・取得結果だけを出す(出力をそのまま貼れるように)。

    ./venv/bin/python verify_share.py [job_id]
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

JOB = sys.argv[1] if len(sys.argv) > 1 else "11b0300e2ab3"
BASE = "http://127.0.0.1:8650"

# 1) 共有APIを叩く
req = urllib.request.Request(f"{BASE}/api/sheet/jobs/{JOB}/share", method="POST")
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
        print(f"POST /share : HTTP {r.status}")
except urllib.error.HTTPError as e:
    print(f"POST /share : HTTP {e.code} {e.reason}")
    print(e.read()[:500].decode("utf-8", "replace"))
    sys.exit(1)
except urllib.error.URLError as e:
    print(f"POST /share : 接続できません ({e.reason})。8650 は起動していますか")
    sys.exit(1)

if "url" not in d:
    print("url が返っていない:", json.dumps(d, ensure_ascii=False)[:300])
    sys.exit(1)

u = urllib.parse.urlparse(d["url"])
q = urllib.parse.parse_qs(u.query)
print(f"host        : {u.hostname}")
print(f"key         : {u.path}")
print(f"署名        : {q.get('X-Amz-Algorithm', ['?'])[0]} / expires={q.get('X-Amz-Expires', ['?'])[0]}s")
print(f"expires_in  : {d.get('expires_in')}")
qr = d.get("qr_data_url") or ""
print(f"QR          : {'あり (%dB)' % len(qr) if qr.startswith('data:image/png') else 'なし'}")

# 2) 署名URLで実際に取れるか(GET向け署名なので Range 付き GET)
get = urllib.request.Request(d["url"], headers={"Range": "bytes=0-1023"})
try:
    with urllib.request.urlopen(get, timeout=60) as r:
        body = r.read()
        print(f"\n実取得      : HTTP {r.status}  {r.headers.get('Content-Type')}")
        print(f"Content-Range : {r.headers.get('Content-Range')}")
        print(f"Disposition   : {r.headers.get('Content-Disposition')}")
        print(f"Cache-Control : {r.headers.get('Cache-Control')}")
        ok = body[:8] == b"\x89PNG\r\n\x1a\n"
        print(f"PNGマジック   : {'OK' if ok else 'NG ' + repr(body[:8])}")
except urllib.error.HTTPError as e:
    print(f"\n実取得      : HTTP {e.code} {e.reason}")
    print(e.read()[:500].decode("utf-8", "replace"))
    sys.exit(1)

# 3) 署名なしでは取れないこと。
#    注意: これはS3 APIエンドポイントが匿名リクエストを受け付けないだけで、
#    バケットの公開設定の検査ではない。公開されているかは r2.dev サブドメインと
#    カスタムドメインの有無で決まるので、R2ダッシュボードで確認すること。
bare = f"{u.scheme}://{u.netloc}{u.path}"
try:
    with urllib.request.urlopen(bare, timeout=30) as r:
        print(f"\n無署名アクセス: HTTP {r.status} ← 署名なしで取れている。設定を見直すこと")
except urllib.error.HTTPError as e:
    print(f"\n無署名アクセス: HTTP {e.code} {e.reason} (署名なしでは取れない = 期待どおり)")
    print("  ※ 公開設定の検査ではない。r2.dev / カスタムドメインの無効は別途確認すること")
except urllib.error.URLError as e:
    print(f"\n無署名アクセス: 到達不能 ({e.reason})")
