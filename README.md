# design-sheet-server — マルチキャラクターシート作成

1枚のキャラクター画像から、**多視点(前/後ろ/左/右/45°) × 変種(彩色イラスト/線画/部分彩色)** の
グリッド状キャラクターシートを生成する Web アプリ。

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

1. **stylize**(任意、既定ON): 入力写真をフラット彩色イラスト化(`/api/edit`)
2. **views**: charsheet ジョブで8方向生成(Multiple-angles LoRA)→ 選択ビューを取得
3. **variants**: 各ビューへ 線画化(`/api/edit` 1回)、部分彩色(**線画への2パス目**)
   - ※1パスで「線画化+部分彩色」を同時指示すると Lightning cfg=1.0 では色指定が
     脱落することを実測済み(2026-09-09)。2パス方式なら線画と部分彩色版の線が
     同一になる利点もある
4. **compose**: PILでグリッド合成(`sheet.png`)+ 全画像ZIP

使用モデル(バックエンド側): Qwen-Image-Edit-2511 + Lightning 4steps
(+ 多視点は fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA)。

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
| `POST /api/sheet/generate` | multipart: `image` 必須。Form: `seed`(-1=ランダム) / `views`(CSV、既定 front,back,left,right,front_left_45,front_right_45) / `variants`(CSV、既定 color,lineart,partial) / `stylize`(bool) / `partial_target` / `partial_color` / `quant`("gguf-q4_k_m" 等、空=バックエンド現状) / `lightning` / `layout`("a4"=A4横300dpi・印刷向け(既定) / "grid"=旧・横長グリッド) / `hero_variant`(A4の主役大判パネル: "lineart"(既定)/"color"/"partial"/"none") / `hero_view`(主役のビュー、既定 "front") |

A4レイアウトは主役あり(既定)のとき「左1/2の上=入力(元画像)・下=主役パネル、右1/2=多視点グリッド」構成。`hero_variant=none` でグリッドのみの中央配置(入力はヘッダ右上のサムネイル)になる。
| `GET /api/sheet/jobs/{id}` | ジョブ状態(セル単位のステータス、charsheet進捗、cell_errors) |
| `GET /api/sheet/jobs/{id}/images/{name}.png` | 個別画像(`input` / `stylized` / `{view}_{variant}`) |
| `GET /api/sheet/jobs/{id}/sheet.png` | 合成シート |
| `GET /api/sheet/jobs/{id}/download.zip` | 全PNGのZIP |
| `GET /api/health` | バックエンド疎通(空きVRAM等) |
| `GET /api/meta` | ビュー/変種の一覧(UI用) |

ビューキー: `front` `back` `left` `right` `front_left_45` `front_right_45` `back_left_45` `back_right_45`
変種キー: `color`(charsheet出力そのまま) `lineart` `partial`

## 環境変数

| 変数 | 既定 | 説明 |
|---|---|---|
| `DS_SHEET_PORT` | 8650 | 本アプリのポート(run.sh) |
| `DS_SHEET_VENV` | ./venv | 使用venv(run.sh) |
| `DS_SHEET_IMAGE_SERVER_URL` | http://127.0.0.1:8620 | バックエンドURL |
| `DS_SHEET_QUANT` | (空) | /api/edit に渡す量子化方式の既定値 |
| `DS_SHEET_EDIT_SIZE` | 0 | 変種パスの生成解像度(0=バックエンド自動推定) |
| `DS_SHEET_PANEL_PX` | 512 | シート合成の1パネル幅 |
| `DS_SHEET_BUSY_RETRIES` / `DS_SHEET_BUSY_RETRY_INTERVAL_S` | 60 / 10.0 | バックエンド409時のリトライ |

## 制約・注意

- **charsheet は8方向固定**(バックエンド側にビュー選択APIが無い)。選択ビューが
  4つでも8方向分の生成時間がかかる。ビュー選択パラメータの追加はバックエンド側の
  改善候補。
- 部分彩色は `partial_target` の言語指定ベース(例: "the overalls")。対象の
  取り違えはプロンプト調整で対処する。
- `quant` は `/api/edit` にのみ渡る。charsheet(多視点)はバックエンド起動時の
  `DS_QUANT` に従う。
- バックエンドがビジー(409)の間は自動リトライで待つ(既定 最大10分)。

## ライセンス

MIT License(LICENSE ファイル参照)。
