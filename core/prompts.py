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


# 出力モード。多視点生成の前に入力をこの見た目へ整える。線画と部分彩色は、
# 多視点生成後にも専用プロンプトを掛けて線と塗りの仕上がりを揃える。
MODE_LABELS = [
    ("real", "リアル"),
    ("anime", "アニメ"),
    ("partial", "部分彩色"),
    ("illustration", "イラスト"),
    ("lineart", "線画"),
    ("chibi", "ちびキャラ"),
]
MODE_KEYS = [k for k, _ in MODE_LABELS]
MODE_LABEL_BY_KEY = dict(MODE_LABELS)

MODE_PROMPTS = {
    "real": (
        "Preserve the character's identity, facial features, hairstyle, costume, colors, "
        "accessories and body proportions exactly. Render the character as a realistic "
        "full-body studio character reference with natural skin and fabric texture, balanced "
        "soft lighting, sharp details, a neutral standing pose, and a pure white background."
    ),
    "anime": (
        "Preserve the character's identity, hairstyle, costume, colors, accessories and body "
        "proportions exactly. Redraw the character as a polished Japanese anime character, "
        "clean expressive linework, crisp cel shading, vivid controlled colors, a neutral "
        "full-body standing pose, and a pure white background."
    ),
    "illustration": (
        "Preserve the character's identity, hairstyle, costume, colors, accessories and body "
        "proportions exactly. Redraw the character as a polished full-body character design "
        "illustration, clean contours, refined digital painting, soft controlled shading, a "
        "neutral standing pose, and a pure white background."
    ),
    "chibi": (
        "Preserve the character's identity, hairstyle, costume, colors and accessories. "
        "Redesign this same person as an adorable super-deformed chibi character with an "
        "oversized expressive head and a small compact body. Use clean anime linework, flat cel "
        "colors and a neutral standing pose. The final image must show exactly one complete "
        "full-body character, centered alone from head to toe on a pure white background."
    ),
    # 多視点生成へ渡す基準画は色と形が明瞭な方が安定するため、線画系も一度
    # フラットな設定画へ正規化し、ビュー生成後に LINEART_PROMPT で仕上げる。
    "lineart": STYLIZE_PROMPT,
    "partial": STYLIZE_PROMPT,
}


def build_mode_prompt(mode: str) -> str:
    if mode not in MODE_PROMPTS:
        raise ValueError(f"未知の表現モードです: {mode}")
    return MODE_PROMPTS[mode]


SIZE_LABELS = [
    ("small", "小"),
    ("medium", "中"),
    ("large", "大"),
]
SIZE_KEYS = [k for k, _ in SIZE_LABELS]
SIZE_LABEL_BY_KEY = dict(SIZE_LABELS)


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
