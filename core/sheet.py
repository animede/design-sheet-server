# -*- coding: utf-8 -*-
"""キャラクターシートの合成(PIL、GPU不使用)。

レイアウト2種:
  - "a4"(既定): A4横(300dpi=3508x2480)に印刷して綺麗に収まる固定キャンバス。
    行=ビュー・列=変種のブロックを1〜2個横に並べ、ビュー数に応じて最適な
    ブロック数とパネルサイズを自動選択して中央配置する。
  - "grid": 旧レイアウト(列=[入力]+ビュー、行=変種)。横長になるため画面確認向き。
各セルは固定パネル枠に「contain」でフィットさせる。
"""
import glob
import math
import os

from PIL import Image, ImageDraw, ImageFont

from core import config
from core.prompts import (
    MODE_LABEL_BY_KEY,
    SIZE_LABEL_BY_KEY,
    VARIANT_LABEL_BY_KEY,
    VIEW_LABEL_BY_KEY,
)

# A4横 300dpi
A4_W, A4_H = 3508, 2480
A4_MARGIN = 118          # 10mm(プリンタの非印字領域を考慮した白フチ)
A4_HEADER_H = 240
A4_ROW_LABEL_W = 230   # 「左前45°」等の長いビュー名が収まる幅
A4_COL_LABEL_H = 56
A4_GUT = 14
A4_BLOCK_GAP = 70
A4_PANEL_MAX = 720

_FONT_PATTERNS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
]


def _load_font(size: int):
    for pat in _FONT_PATTERNS:
        for p in glob.glob(pat):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _fit_panel(img: Image.Image, pw: int, ph: int) -> Image.Image:
    """白背景のパネル枠(pw×ph)へアスペクト維持で収める。"""
    panel = Image.new("RGB", (pw, ph), "white")
    img = img.convert("RGB")
    scale = min(pw / img.width, ph / img.height)
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    img = img.resize((w, h), Image.LANCZOS)
    panel.paste(img, ((pw - w) // 2, (ph - h) // 2))
    return panel


def _draw_panel(sheet, d, path, x, y, pw, ph, strong_border=True):
    """1セル分を描画する(存在しなければ「-」プレースホルダ)。"""
    if path and os.path.exists(path):
        sheet.paste(_fit_panel(Image.open(path), pw, ph), (x, y))
        d.rectangle([x - 1, y - 1, x + pw, y + ph], outline=(210, 210, 210), width=1)
    else:
        d.rectangle([x - 1, y - 1, x + pw, y + ph], outline=(235, 235, 235), width=1)
        d.text((x + pw / 2 - 6, y + ph / 2 - 15), "-", fill=(200, 200, 200))


def compose_mode_sheet(
    job_dir: str,
    title: str,
    views: list,
    mode: str,
    sizes: list,
    layout: str = "a4",
) -> str:
    """1つの表現モードを、多視点 × 表示サイズでまとめた設定シートを作る。

    サイズは画像の再生成解像度ではなく、シート上のキャラクター表示倍率を表す。
    同一ビューを小・中・大で置くので、アイコンから立ち絵までの見え方を1枚で確認できる。
    """
    ratios = {"small": 0.48, "medium": 0.72, "large": 1.0}
    sizes = [s for s in sizes if s in ratios]
    if not sizes:
        sizes = ["large"]

    if layout == "grid":
        cols = min(3, max(1, len(views)))
        rows = math.ceil(len(views) / cols)
        W = 2100
        margin, gap, header_h = 60, 26, 150
        group_w = (W - margin * 2 - gap * (cols - 1)) // cols
        group_h = 560
        H = margin * 2 + header_h + rows * group_h + (rows - 1) * gap
    else:
        W, H = A4_W, A4_H
        margin, gap, header_h = A4_MARGIN, 28, 230
        body_w = W - margin * 2
        body_h = H - margin * 2 - header_h
        best = None
        for cols_try in range(1, min(4, len(views)) + 1):
            rows_try = math.ceil(len(views) / cols_try)
            gw = (body_w - gap * (cols_try - 1)) / cols_try
            gh = (body_h - gap * (rows_try - 1)) / rows_try
            score = min(gw, gh * 1.55)
            if best is None or score > best[0]:
                best = (score, cols_try, rows_try, gw, gh)
        _, cols, rows, group_w, group_h = best
        group_w, group_h = int(group_w), int(group_h)

    canvas = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(canvas)
    f_title = _load_font(58 if layout != "grid" else 42)
    f_sub = _load_font(31 if layout != "grid" else 25)
    f_label = _load_font(28 if layout != "grid" else 23)
    d.text((margin, margin), title, fill=(35, 35, 35), font=f_title)
    mode_label = MODE_LABEL_BY_KEY.get(mode, mode)
    size_text = "・".join(SIZE_LABEL_BY_KEY.get(s, s) for s in sizes)
    d.text((margin, margin + 82), f"表現: {mode_label}    サイズ: {size_text}",
           fill=(110, 110, 110), font=f_sub)

    total_ratio = sum(ratios[s] for s in sizes)
    inner_gap = 10
    label_h = 42
    for i, view in enumerate(views):
        row, col = divmod(i, cols)
        gx = int(margin + col * (group_w + gap))
        gy = int(margin + header_h + row * (group_h + gap))
        d.rounded_rectangle(
            [gx, gy, gx + group_w, gy + group_h], radius=16,
            fill=(250, 250, 250), outline=(218, 218, 218), width=2,
        )
        vlabel = VIEW_LABEL_BY_KEY.get(view, view)
        d.text((gx + 18, gy + 12), vlabel, fill=(65, 65, 65), font=f_label)

        content_top = gy + 58
        content_h = max(40, group_h - 72)
        usable_w = group_w - 28 - inner_gap * (len(sizes) - 1)
        x = gx + 14
        path = os.path.join(job_dir, f"{view}_{mode}.png")
        for s in sizes:
            slot_w = max(20, int(usable_w * ratios[s] / total_ratio))
            # キャラクター自体の寸法を段階的に変え、各スロットの下端で揃える。
            side = max(20, min(slot_w, int((content_h - label_h) * ratios[s])))
            px = x + (slot_w - side) // 2
            py = content_top + content_h - label_h - side
            if os.path.exists(path):
                canvas.paste(_fit_panel(Image.open(path), side, side), (px, py))
                d.rectangle([px, py, px + side, py + side], outline=(225, 225, 225), width=1)
            else:
                d.rectangle([px, py, px + side, py + side], outline=(225, 225, 225), width=1)
            slabel = SIZE_LABEL_BY_KEY.get(s, s)
            tw = d.textlength(slabel, font=f_label)
            d.text((x + (slot_w - tw) / 2, gy + group_h - label_h + 2),
                   slabel, fill=(135, 135, 135), font=f_label)
            x += slot_w + inner_gap

    out_path = os.path.join(job_dir, "sheet.png")
    if layout == "grid":
        canvas.save(out_path)
    else:
        canvas.save(out_path, dpi=(300, 300))
    return out_path


def compose_sheet_a4(
    job_dir: str,
    title: str,
    views: list,
    variants: list,
    hero_view: str = None,
    hero_variant: str = None,
) -> str:
    """A4横(300dpi)キャンバスへ合成する。

    ヒーローあり(既定): 左1/2を上下に分割し「上=入力(元画像)、下=主役(既定は線画)」を
    大判で置き、右1/2に単一ブロックの多視点グリッド(行=ビュー、列=変種)を置く。
    ヒーローなし(hero_variant="none"): 1〜2ブロックのグリッドのみを中央配置し、
    入力はヘッダ右上のサムネイルにする。
    """
    n_views, n_cols = len(views), len(variants)

    hero_path = None
    if hero_view and hero_variant:
        p = os.path.join(job_dir, f"{hero_view}_{hero_variant}.png")
        if os.path.exists(p):
            hero_path = p

    body_top = A4_MARGIN + A4_HEADER_H
    body_h = A4_H - A4_MARGIN - body_top
    avail_w = A4_W - 2 * A4_MARGIN

    sheet = Image.new("RGB", (A4_W, A4_H), "white")
    d = ImageDraw.Draw(sheet)
    f_title = _load_font(64)
    f_sub = _load_font(36)
    f_label = _load_font(40)

    # ヘッダ: タイトル + サブ情報
    d.text((A4_MARGIN, A4_MARGIN), title, fill=(35, 35, 35), font=f_title)
    sub = "views: " + ", ".join(VIEW_LABEL_BY_KEY.get(v, v) for v in views)
    d.text((A4_MARGIN, A4_MARGIN + 92), sub, fill=(120, 120, 120), font=f_sub)
    input_path = os.path.join(job_dir, "input.png")

    def draw_block(bx, y0, block_views, panel):
        """1ブロック分(列ヘッダ+行ラベル+セル)を描画する。"""
        for ci, variant in enumerate(variants):
            label = VARIANT_LABEL_BY_KEY.get(variant, variant)
            cx = bx + A4_ROW_LABEL_W + ci * (panel + A4_GUT)
            tw = d.textlength(label, font=f_label)
            d.text((cx + (panel - tw) / 2, y0), label, fill=(90, 90, 90), font=f_label)
        for ri, view in enumerate(block_views):
            cy = y0 + A4_COL_LABEL_H + ri * (panel + A4_GUT)
            d.text((bx, cy + panel / 2 - 22), VIEW_LABEL_BY_KEY.get(view, view),
                   fill=(90, 90, 90), font=f_label)
            for ci, variant in enumerate(variants):
                cx = bx + A4_ROW_LABEL_W + ci * (panel + A4_GUT)
                _draw_panel(sheet, d, os.path.join(job_dir, f"{view}_{variant}.png"),
                            cx, cy, panel, panel)

    if hero_path:
        # --- 左カラム: 上=入力(元画像)、下=主役。幅は画像の実表示幅ぴったりに詰める ---
        cap_h = 48
        cell_h = (body_h - 2 * cap_h - 2 * A4_GUT) // 2

        def _disp_w(path):
            with Image.open(path) as im:
                return round(cell_h * im.width / im.height)

        w_in = _disp_w(input_path) if os.path.exists(input_path) else 0
        w_hero = _disp_w(hero_path)
        left_w = min(max(w_in, w_hero, 400), 1400)

        def _draw_tight(path, y, caption):
            """枠を画像実寸に密着させて描く(左カラム内で中央寄せ、縦もセル内中央)。"""
            img = Image.open(path).convert("RGB")
            scale = min(cell_h / img.height, left_w / img.width)
            w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
            img = img.resize((w, h), Image.LANCZOS)
            x = A4_MARGIN + (left_w - w) // 2
            yy = y + (cell_h - h) // 2
            sheet.paste(img, (x, yy))
            d.rectangle([x - 1, yy - 1, x + w, yy + h], outline=(210, 210, 210), width=1)
            d.text((x, yy + h + 6), caption, fill=(150, 150, 150), font=f_sub)

        if os.path.exists(input_path):
            _draw_tight(input_path, body_top, "入力(元画像)")
        hy = body_top + cell_h + cap_h + 2 * A4_GUT
        hero_label = (f"主役: {VIEW_LABEL_BY_KEY.get(hero_view, hero_view)} / "
                      f"{VARIANT_LABEL_BY_KEY.get(hero_variant, hero_variant)}")
        _draw_tight(hero_path, hy, hero_label)

        # --- 右カラム: 残り幅全体で1〜2ブロックを最適化して大きく配置 ---
        # ビュー名は左ラベル列ではなく各行の下キャプションに置く(横幅をパネルに全振り。
        # 縦は8ビューでも余裕があるためキャプション分は実質無料)。
        right_x = A4_MARGIN + left_w + A4_BLOCK_GAP
        right_w = avail_w - left_w - A4_BLOCK_GAP
        row_cap = 44
        best = None
        for b in (1, 2):
            if n_views < b:
                continue
            rows_b = math.ceil(n_views / b)
            p_h = (body_h - A4_COL_LABEL_H - rows_b * row_cap
                   - (rows_b - 1) * A4_GUT) / rows_b
            aw = right_w - b * (n_cols - 1) * A4_GUT - (b - 1) * A4_BLOCK_GAP
            pnl = min(p_h, aw / (b * n_cols), A4_PANEL_MAX)
            if best is None or pnl > best[0]:
                best = (pnl, b, rows_b)
        panel, blocks, rows = int(best[0]), best[1], best[2]
        block_w = n_cols * panel + (n_cols - 1) * A4_GUT
        used_w = blocks * block_w + (blocks - 1) * A4_BLOCK_GAP
        used_h = A4_COL_LABEL_H + rows * (panel + row_cap) + (rows - 1) * A4_GUT
        x0 = right_x + max(0, (right_w - used_w) // 2)
        y0 = body_top + max(0, (body_h - used_h) // 2)
        for bi in range(blocks):
            bx = x0 + bi * (block_w + A4_BLOCK_GAP)
            block_views = views[bi * rows:(bi + 1) * rows]
            for ci, variant in enumerate(variants):
                label = VARIANT_LABEL_BY_KEY.get(variant, variant)
                cx = bx + ci * (panel + A4_GUT)
                tw = d.textlength(label, font=f_label)
                d.text((cx + (panel - tw) / 2, y0), label, fill=(90, 90, 90), font=f_label)
            for ri, view in enumerate(block_views):
                cy = y0 + A4_COL_LABEL_H + ri * (panel + row_cap + A4_GUT)
                for ci, variant in enumerate(variants):
                    cx = bx + ci * (panel + A4_GUT)
                    _draw_panel(sheet, d, os.path.join(job_dir, f"{view}_{variant}.png"),
                                cx, cy, panel, panel)
                vlabel = VIEW_LABEL_BY_KEY.get(view, view)
                tw = d.textlength(vlabel, font=f_sub)
                d.text((bx + (block_w - tw) / 2, cy + panel + 4),
                       vlabel, fill=(130, 130, 130), font=f_sub)
    else:
        # --- ヒーローなし: 1〜2ブロックを中央配置、入力はヘッダ右上サムネイル ---
        if os.path.exists(input_path):
            thumb_h = A4_HEADER_H - 30
            img = Image.open(input_path).convert("RGB")
            tw = max(1, round(img.width * thumb_h / img.height))
            img = img.resize((tw, thumb_h), Image.LANCZOS)
            tx = A4_W - A4_MARGIN - tw
            sheet.paste(img, (tx, A4_MARGIN))
            d.rectangle([tx - 1, A4_MARGIN - 1, tx + tw, A4_MARGIN + thumb_h],
                        outline=(210, 210, 210), width=1)
            d.text((tx, A4_MARGIN + thumb_h + 6), "入力", fill=(150, 150, 150), font=f_sub)

        best = None
        for b in (1, 2):
            if n_views < b:
                continue
            rows_b = math.ceil(n_views / b)
            avail_h = body_h - A4_COL_LABEL_H - (rows_b - 1) * A4_GUT
            aw = (avail_w - b * A4_ROW_LABEL_W
                  - b * (n_cols - 1) * A4_GUT - (b - 1) * A4_BLOCK_GAP)
            pnl = min(avail_h / rows_b, aw / (b * n_cols), A4_PANEL_MAX)
            if best is None or pnl > best[0]:
                best = (pnl, b, rows_b)
        panel, blocks, rows = int(best[0]), best[1], best[2]

        block_w = A4_ROW_LABEL_W + n_cols * panel + (n_cols - 1) * A4_GUT
        used_w = blocks * block_w + (blocks - 1) * A4_BLOCK_GAP
        used_h = A4_COL_LABEL_H + rows * panel + (rows - 1) * A4_GUT
        x_start = (A4_W - used_w) // 2
        y_start = body_top + max(0, (body_h - used_h) // 2)
        for bi in range(blocks):
            bx = x_start + bi * (block_w + A4_BLOCK_GAP)
            draw_block(bx, y_start, views[bi * rows:(bi + 1) * rows], panel)

    out_path = os.path.join(job_dir, "sheet.png")
    sheet.save(out_path, dpi=(300, 300))
    return out_path


def compose_sheet(
    job_dir: str,
    title: str,
    views: list,
    variants: list,
    include_input: bool = True,
    layout: str = "a4",
    hero_view: str = None,
    hero_variant: str = None,
) -> str:
    """job_dir 内の画像からシートを合成して sheet.png のパスを返す。

    期待するファイル名: input.png / {view}_{variant}.png
    存在しないセルは空白(薄グレーの「-」)のまま進める。
    layout: "a4"(既定、A4横300dpi) / "grid"(旧・横長グリッド)。
    hero_view/hero_variant: a4レイアウトの主役パネル(大判)。grid では無視。
    """
    if layout != "grid":
        return compose_sheet_a4(job_dir, title, views, variants,
                                hero_view=hero_view, hero_variant=hero_variant)
    pw = ph = config.SHEET_PANEL_PX
    margin, gut = 48, 20
    title_h, col_h, row_w, cap_h = 72, 48, 140, 0

    cols = (["__input__"] if include_input else []) + list(views)
    rows = list(variants)

    W = margin * 2 + row_w + len(cols) * pw + (len(cols) - 1) * gut
    H = margin * 2 + title_h + col_h + len(rows) * ph + (len(rows) - 1) * gut + cap_h

    sheet = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(sheet)
    f_title = _load_font(34)
    f_label = _load_font(26)

    d.text((margin, margin), title, fill=(40, 40, 40), font=f_title)

    x0 = margin + row_w
    y0 = margin + title_h + col_h

    # 列ヘッダ
    for ci, col in enumerate(cols):
        label = "入力" if col == "__input__" else VIEW_LABEL_BY_KEY.get(col, col)
        cx = x0 + ci * (pw + gut)
        tw = d.textlength(label, font=f_label)
        d.text((cx + (pw - tw) / 2, margin + title_h + 8), label, fill=(70, 70, 70), font=f_label)

    # 行ラベル + セル
    for ri, variant in enumerate(rows):
        cy = y0 + ri * (ph + gut)
        vlabel = VARIANT_LABEL_BY_KEY.get(variant, variant)
        d.text((margin, cy + ph / 2 - 15), vlabel, fill=(70, 70, 70), font=f_label)
        for ci, col in enumerate(cols):
            cx = x0 + ci * (pw + gut)
            if col == "__input__":
                # 入力列は最上段のみ(元画像は変種を持たない)
                path = os.path.join(job_dir, "input.png") if ri == 0 else None
            else:
                path = os.path.join(job_dir, f"{col}_{variant}.png")
            if path and os.path.exists(path):
                panel = _fit_panel(Image.open(path), pw, ph)
                sheet.paste(panel, (cx, cy))
                d.rectangle([cx - 1, cy - 1, cx + pw, cy + ph], outline=(210, 210, 210), width=1)
            elif col != "__input__" or ri == 0:
                d.rectangle([cx - 1, cy - 1, cx + pw, cy + ph], outline=(235, 235, 235), width=1)
                d.text((cx + pw / 2 - 6, cy + ph / 2 - 15), "-", fill=(200, 200, 200), font=f_label)

    out_path = os.path.join(job_dir, "sheet.png")
    sheet.save(out_path)
    return out_path
