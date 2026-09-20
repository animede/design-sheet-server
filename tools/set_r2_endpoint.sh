#!/bin/bash
# .env の DS_SHEET_R2_ENDPOINT を実アカウントIDで書き換える。
#
# 値は引数か、無ければ端末(/dev/tty)から直接読む。パイプや貼り付けの巻き添えで
# 空文字が入るのを避けるため、標準入力は使わない。
set -u

ENV_FILE="${DS_ENV_FILE:-$HOME/design-sheet-server/.env}"

if [ ! -f "$ENV_FILE" ]; then
  echo "見つかりません: $ENV_FILE" >&2
  exit 1
fi

IN="${1:-}"
if [ -z "$IN" ]; then
  printf 'Cloudflare のアカウントID か S3 API URL を貼り付けて Enter: '
  IFS= read -r IN < /dev/tty
fi

ACC=$(printf '%s' "$IN" | grep -oiE '[0-9a-f]{32}' | head -1 | tr 'A-Z' 'a-z')
if [ -z "$ACC" ]; then
  echo "32桁の16進が見つかりませんでした。入力: [$IN]" >&2
  echo "例: https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com/design-sheet-share" >&2
  exit 1
fi

sed -i "s#^DS_SHEET_R2_ENDPOINT=.*#DS_SHEET_R2_ENDPOINT=https://$ACC.r2.cloudflarestorage.com#" "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "書き換えました:"
grep '^DS_SHEET_R2_ENDPOINT' "$ENV_FILE"
