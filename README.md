# design-sheet-server — マルチキャラクターシート作成

1枚のキャラクター画像から、**表現モード × 多視点 × 表示サイズ** の
キャラクターデザインシートを生成する Web アプリ。

- 表現モード: リアル / アニメ / 部分彩色 / イラスト / 線画 / ちびキャラ
- ビュー: 前 / 後ろ / 左右 / 前後45°
- 表示サイズ: 小 / 中 / 大(複数選択可)
- 生成サイズ: デモ高速(多視点624px・仕上げ512px、UI既定) / 速度優先(多視点768px・仕上げ512px) / 品質優先(従来どおり)

「デモ高速」は短時間デモ専用の簡易出力ではない。全身構図を守る安全余白と
プロンプト補強を入れた、通常のキャラクターシート制作にも使える推奨モード。
細部をより重視する場合だけ「速度優先」または「品質優先」へ切り替える。

UIの既定値は **ちびキャラ / デモ高速 / シート内サイズ「大」のみ**。

## アーキテクチャ

本アプリは **GPUを一切使わない薄いオーケストレーションサーバ**(port 8650)。
画像生成はすべて **diffusers-image-server**(既定 `http://127.0.0.1:8620`)へHTTP委譲する。

```
[UI/API 8650] ──HTTP──> [diffusers-image-server 8620]
  ジョブ管理                /api/edit          (イラスト化・線画・部分彩色)
  2パス部分彩色の制御        /api/charsheet/*   (多視点8方向、Edit+angles adapter)
  PILシート合成
```

パイプライン(ジョブ式、同時1件):

1. **styling**: 入力を選択した表現モードの基準画像へ変換(`/api/edit`)
2. **views**: charsheet ジョブで選択した方向だけを生成(Multiple-angles LoRA)
3. **finishing**: 線画・部分彩色モードだけ各ビューを追加変換。部分彩色は線画への2パス目
   - ※1パスで「線画化+部分彩色」を同時指示すると Lightning cfg=1.0 では色指定が
     脱落することを実測済み(2026-09-09)。2パス方式なら線画と部分彩色版の線が
     同一になる利点もある
4. **compose**: 同じビューを選択した小・中・大の倍率で配置(`sheet.png`)+ 全画像ZIP

多視点生成中は、完成した方向から個別画像を取得して結果グリッドへ順次表示する。
線画・部分彩色は多視点生成後の仕上げが終わったセルから順次表示する。

使用モデル(バックエンド側): Qwen-Image-Edit-2511 + Lightning 4steps
(+ 多視点は fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA)。

## 生成モードの使い分け

| UI表示 | API値 | 多視点 | 仕上げ | 推奨用途 |
|---|---|---:|---:|---|
| デモ高速（多視点624px） | `demo` | 624px | 512px | 通常運用、試作、デモ、反復生成。UI既定 |
| 速度優先（多視点768px） | `fast` | 768px | 512px | 衣装・髪・小物の細部を624pxより残したい場合 |
| 品質優先（従来サイズ） | `quality` | バックエンド自動 | バックエンド自動 | 最終素材や細部優先。時間とVRAM消費は最大 |

`demo` は参照画像へ30%の白い安全余白を加え、`wide shot`と「頭頂から足先まで、
四辺に余白を残す」指定を併用する。624pxは現在の48GB GPU構成で多視点生成時の
不要なCPU↔GPU転送を避けられるため、解像度差以上に高速になる。`fast`も同じ
全身指定を使うが、安全余白は15%。基準となる最初のスタイル画像はどちらも
従来解像度を保つ。

同一入力・seed・ちびキャラ・6方向・モデル読込済みでの実測例:

| モード | 完了時間 |
|---|---:|
| デモ高速 624px | 48.4秒 |
| 速度優先 768px | 101.5秒 |

上記はこのサーバーでの一例で、入力解像度、GPU、モデルのロード状態で変動する。
バックエンド再起動後の初回はモデルロードが加わり、実測では約126秒だった。
デモや連続運用の前に一度生成してウォームアップしておくと、その後の時間が安定する。

## セットアップ・起動

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
./run.sh                # 既定 port 8650
# 既存venvを共有する場合: DS_SHEET_VENV=<venvのパス> ./run.sh
```

バックエンド(diffusers-image-server)が起動していること。24GB級GPUで動かす場合の
バックエンド起動例:

```bash
cd <diffusers-image-serverのディレクトリ>
CUDA_VISIBLE_DEVICES=1 DS_QUANT=gguf-q4_k_m DS_OFFLOAD=model_cpu \
  venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8621
# 本アプリ側: DS_SHEET_IMAGE_SERVER_URL=http://127.0.0.1:8621 ./run.sh
```

## API

| エンドポイント | 説明 |
|---|---|
| `POST /api/sheet/generate` | multipart: `image` 必須。Form: `seed`(-1=ランダム) / `mode`(`real`,`anime`,`partial`,`illustration`,`lineart`,`chibi`) / `views`(CSV) / `sizes`(`small`,`medium`,`large` のCSV) / `generation_mode`(`demo`,`quality`,`fast`) / `partial_target` / `partial_color` / `quant` / `lightning` / `layout`(`a4` または `grid`)。UIは`demo`を送る。APIで省略した場合は後方互換のため`quality`。`mode`未指定時は旧`variants` APIとして処理 |
| `GET /api/sheet/jobs/{id}` | ジョブ状態(セル単位のステータス、charsheet進捗、cell_errors) |
| `GET /api/sheet/jobs/{id}/images/{name}.png` | 個別画像(`input` / `stylized` / `{view}_{variant}`) |
| `GET /api/sheet/jobs/{id}/sheet.png` | 合成シート |
| `GET /api/sheet/jobs/{id}/download.zip` | 全PNGのZIP |
| `POST /api/sheet/jobs/{id}/share` | 完成シートをR2へアップロードし、期限付きURLとQR画像を返す |
| `GET /api/health` | バックエンド疎通(空きVRAM等) |
| `GET /api/meta` | ビュー/表現モード/表示サイズの一覧(UI用) |

ビューキー: `front` `back` `left` `right` `front_left_45` `front_right_45` `back_left_45` `back_right_45`
表示サイズはシート上の倍率で、生成画像そのものを低解像度化する指定ではない。
生成サイズの`demo`と`fast`は、全身構図を決める最初のスタイル基準画だけ従来解像度を
保ち、多視点と追加仕上げを上表のサイズへ抑える。`quality`は全工程で従来解像度と
`medium shot`構図を保つ。

## 環境変数

| 変数 | 既定 | 説明 |
|---|---|---|
| `DS_SHEET_PORT` | 8650 | 本アプリのポート(run.sh) |
| `DS_SHEET_VENV` | ./venv | 使用venv(run.sh) |
| `DS_SHEET_IMAGE_SERVER_URL` | http://127.0.0.1:8620 | バックエンドURL |
| `DS_SHEET_QUANT` | (空) | /api/edit に渡す量子化方式の既定値 |
| `DS_SHEET_EDIT_SIZE` | 0 | 基準画・品質優先時の生成解像度(0=バックエンド自動推定)。`demo`/`fast`の固定多視点サイズには影響しない |
| `DS_SHEET_PANEL_PX` | 512 | シート合成の1パネル幅 |
| `DS_SHEET_BUSY_RETRIES` / `DS_SHEET_BUSY_RETRY_INTERVAL_S` | 60 / 10.0 | バックエンド409時のリトライ |
| `DS_SHEET_R2_ENDPOINT` | (空) | R2のS3 API endpoint。共有機能の有効化に必須 |
| `DS_SHEET_R2_BUCKET` | (空) | アップロード先R2バケット名 |
| `DS_SHEET_R2_ACCESS_KEY_ID` | (空) | バケット限定のR2 Access Key ID |
| `DS_SHEET_R2_SECRET_ACCESS_KEY` | (空) | バケット限定のR2 Secret Access Key |
| `DS_SHEET_R2_URL_TTL_S` | 86400 | QRコードの期限付きURLの有効秒数(60～604800) |

### Cloudflare R2 でスマホへ共有

上記4つの必須環境変数を設定すると、生成結果に「クラウド共有用QRコードを作成」
ボタンが表示される。押した時だけ `sheet.png` を非公開バケットへアップロードし、
既定24時間有効の署名付きダウンロードURLをQRコードとして表示する。

設定例は `.env.example` を `.env` へコピーして使う。`.env` は起動時に自動読込され、
Git管理対象外になっている。`chmod 600 .env` で閲覧権限を制限し、設定後はアプリを
再起動する。

**`DS_SHEET_R2_ENDPOINT` の `ACCOUNT_ID` は実アカウントIDへ必ず置き換える**
(Cloudflareダッシュボード → R2 → バケット →「S3 API」のホスト部分)。プレースホルダの
ままだと共有は無効のままで、起動ログに理由が出る:

```
[design-sheet] Cloudflare R2共有は無効: .env.example のプレースホルダ ACCOUNT_ID が残っている
```

認証情報をソースコードやGitへ保存しないこと。R2 APIトークンは対象バケットだけの
`Object Read & Write` 権限に限定する。URL期限はオブジェクト削除ではないため、R2側で
`design-sheets/` プレフィックスを1～2日後に削除するObject lifecycle ruleも設定する。

## テスト

```bash
./venv/bin/python -m unittest tests.test_cloud_share tests.test_share_api tests.test_config tests.test_prompts tests.test_generation_mode
```

`tests/` に `__init__.py` を置いていないため、`unittest discover` ではなくモジュール指定で実行する。

## tools/

R2共有のセットアップと切り分け用。いずれもリポジトリ直下から実行する。

| スクリプト | 用途 |
|---|---|
| `tools/set_r2_endpoint.sh '<ID または S3 API URL>'` | `.env` の `DS_SHEET_R2_ENDPOINT` を実アカウントIDへ書き換える(形式を検証し、不正なら書き換えない) |
| `./venv/bin/python tools/diagnose_r2.py` | 一覧・参照・書き込み・削除を個別に試し、権限とバケット名のどちらが原因かを切り分ける |
| `./venv/bin/python tools/verify_share.py <job_id>` | 実アップロード→署名URLでの取得までを通しで検証する。署名URLは表示しない |

## 制約・注意

- charsheetバックエンドへ選択ビューだけを渡す。既定6方向なら8方向固定時より2方向分、
  4方向選択なら4方向分の生成を省略できる。
- 部分彩色は `partial_target` の言語指定ベース(例: "the overalls")。対象の
  取り違えはプロンプト調整で対処する。
- `quant` は `/api/edit` にのみ渡る。charsheet(多視点)はバックエンド起動時の
  `DS_QUANT` に従う。
- バックエンドがビジー(409)の間は自動リトライで待つ(既定 最大10分)。

## ライセンス

MIT License(LICENSE ファイル参照)。
