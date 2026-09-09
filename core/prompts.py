# -*- coding: utf-8 -*-
"""変種パスのプロンプト定義。

3種とも 2026-09-09 の実測プローブ(diffusers-server outputs/edit_20260909_124*.png、
Qwen-Image-Edit-2511 GGUF Q4_K_M + Lightning 4steps)で品質確認済みの文言がベース。
運用知見(diffusers-server CLAUDE.md 57番): プロンプトは「末尾が最強」なので、
維持句を前置きに、効かせたい指示を末尾に置く。否定形は使わない。
"""

# 写真 → フラット彩色イラスト化(任意の前段パス)
STYLIZE_PROMPT = (
    "Keep the character's design, proportions, pose, costume, colors and accessories "
    "exactly the same, and redraw the image as a flat anime-style character "
    "illustration: clean black outlines, flat cel colors, pure white background, "
    "no photographic texture, cute mascot style."
)

# 任意画像 → 塗り絵スタイルの線画
LINEART_PROMPT = (
    "Keep the character's design, proportions, pose, costume and accessories exactly "
    "the same, and convert the image into a clean line art illustration: crisp uniform "
    "black outlines on a pure white background, no colors, no shading, no gradients, "
    "like a coloring book page."
)

# 線画 → 部分彩色(2パス方式。1パスで「線画化+部分彩色」を同時指示すると
# Lightning cfg=1.0 では色指定が脱落することを実測済みのため、必ず線画に対して掛ける)
# プローブで観測したフレーミングドリフト対策として same framing and scale を維持句に含む。
PARTIAL_COLOR_TEMPLATE = (
    "Keep the line art drawing exactly the same, same lines, same pose, same framing "
    "and scale, same white background, and fill only {target} with a flat {color}; "
    "everything else stays pure white uncolored."
)

DEFAULT_PARTIAL_TARGET = "the clothes and outfit"
DEFAULT_PARTIAL_COLOR = "soft lavender purple color"


def build_partial_prompt(target: str, color: str) -> str:
    target = (target or DEFAULT_PARTIAL_TARGET).strip()
    color = (color or DEFAULT_PARTIAL_COLOR).strip()
    return PARTIAL_COLOR_TEMPLATE.format(target=target, color=color)


# ビュー定義(diffusers-image-server apps/charsheet/prompts.py の VIEWS と同じキー)
VIEW_LABELS = [
    ("front", "前"),
    ("back", "後ろ"),
    ("left", "左"),
    ("right", "右"),
    ("front_left_45", "左前45°"),
    ("front_right_45", "右前45°"),
    ("back_left_45", "左後45°"),
    ("back_right_45", "右後45°"),
]
VIEW_KEYS = [k for k, _ in VIEW_LABELS]
VIEW_LABEL_BY_KEY = dict(VIEW_LABELS)

VARIANT_LABELS = [
    ("color", "彩色"),
    ("lineart", "線画"),
    ("partial", "部分彩色"),
]
VARIANT_KEYS = [k for k, _ in VARIANT_LABELS]
VARIANT_LABEL_BY_KEY = dict(VARIANT_LABELS)
