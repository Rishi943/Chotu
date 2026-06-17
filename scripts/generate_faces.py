#!/usr/bin/env python3
"""
generate_faces.py — Convert Chotu kaomoji expressions to 128×64 1-bit PNGs.

Run on Voyager (not Pi). Output PNGs go to ./faces/ directory.
Copy the entire faces/ directory to the Pi alongside face.py.

Usage:
    python3 generate_faces.py
    python3 generate_faces.py --preview   # opens each image for visual check

Requirements:
    pip install Pillow
"""

import argparse
import os
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Font paths — from your fc-list output on Voyager
# Order matters: fonts are tried in sequence per character until a glyph is found.
# CJK covers the Japanese kaomoji chars; Symbols covers box-drawing / arrows.
# ---------------------------------------------------------------------------

FONT_PATHS = [
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Light.ttc",       # Japanese/Chinese/Korean
    "/usr/share/fonts/noto/NotoSansSymbols-Black.ttf",         # Box drawing, arrows, misc symbols
    "/usr/share/fonts/noto/NotoSansSymbols2-Regular.ttf",      # Supplemental symbols (if present)
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",              # Latin fallback
]

# Remove paths that don't exist on this machine
FONT_PATHS = [p for p in FONT_PATHS if os.path.exists(p)]

# ---------------------------------------------------------------------------
# Canvas config
# ---------------------------------------------------------------------------

WIDTH, HEIGHT = 128, 64
FONT_SIZE = 13          # fits most kaomoji on one line at 128px wide
BG = 0                  # black (OLED off = black)
FG = 255                # white (OLED on = white)

# ---------------------------------------------------------------------------
# Expression definitions
# name -> kaomoji string
# ---------------------------------------------------------------------------

EXPRESSIONS = {
    "idle":          "(￣▽￣)",
    "speak_open":    "(￣▽￣)",      # same as idle — mouth open variant
    "speak_close":   "(￣ ￣)",      # mouth closed — alternate animation frame
    "playful":       "ԅ(≖‿≖ԅ)",
    "judging":       "( ಠ ʖ̯ ಠ)",
    "embarrassed":   "(//ω//)",
    "dissatisfied":  "(￣ヘ￣)",
    "angry":         "(╬ Ò﹏Ó)",
    "sad":           "(╥﹏╥)",
    "indifferent":   "┐(￣ヘ￣)┌",
    "confused":      "(・・ ) ?",
    "doubt":         "(⇀_⇀)",
    "surprised":     "ヽ(°〇°)ﾉ",
    "greeting":      "(￣▽￣)ノ",
    "wink":          "(｡•̀ᴗ-)✧",
    "sleeping":      "(￣o￣) zzZ",
    "magic":         "(∩｀-´)⊃",
    "cute":          "(◕‿◕✿)",
    "thinking":      "(╭ರ_•́)",
    "dead":          "(✖╭╮✖)",
}

# ---------------------------------------------------------------------------
# Font loading
# ---------------------------------------------------------------------------

def load_fonts(size: int) -> list[ImageFont.FreeTypeFont]:
    """Load all available fonts at the given size."""
    fonts = []
    for path in FONT_PATHS:
        try:
            fonts.append(ImageFont.truetype(path, size))
            print(f"  [font] loaded: {Path(path).name} @ {size}px")
        except Exception as e:
            print(f"  [font] SKIP {Path(path).name}: {e}")
    if not fonts:
        print("  [font] WARNING: no fonts loaded — falling back to PIL default (will look bad)")
        fonts.append(ImageFont.load_default())
    return fonts


def char_has_glyph(font: ImageFont.FreeTypeFont, char: str) -> bool:
    """Return True if the font has a real glyph for this character (not .notdef)."""
    try:
        # getmask returns a non-empty mask if the font has the glyph
        mask = font.getmask(char)
        return mask.size[0] > 0 and mask.size[1] > 0
    except Exception:
        return False


def render_text_multifont(
    draw: ImageDraw.ImageDraw,
    text: str,
    fonts: list[ImageFont.FreeTypeFont],
    x: int,
    y: int,
    fill: int = FG,
) -> None:
    """
    Render text character-by-character, picking the first font that has each glyph.
    Falls back to the last font in the list if nothing matches.
    """
    cursor_x = x
    fallback = fonts[-1]

    for char in text:
        chosen = fallback
        for font in fonts:
            if char_has_glyph(font, char):
                chosen = font
                break

        # Draw this character
        draw.text((cursor_x, y), char, font=chosen, fill=fill)

        # Advance cursor by this character's width
        bbox = chosen.getbbox(char)
        if bbox:
            cursor_x += bbox[2] - bbox[0]
        else:
            cursor_x += chosen.size // 2  # rough fallback advance


def measure_text_multifont(
    text: str,
    fonts: list[ImageFont.FreeTypeFont],
) -> tuple[int, int]:
    """Measure total width and max height of text rendered with multifont."""
    total_w = 0
    max_h = 0
    fallback = fonts[-1]
    for char in text:
        chosen = fallback
        for font in fonts:
            if char_has_glyph(font, char):
                chosen = font
                break
        bbox = chosen.getbbox(char)
        if bbox:
            total_w += bbox[2] - bbox[0]
            max_h = max(max_h, bbox[3] - bbox[1])
        else:
            total_w += chosen.size // 2
    return total_w, max_h

# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------

def make_face_image(kaomoji: str, fonts: list[ImageFont.FreeTypeFont]) -> Image.Image:
    """Render a single kaomoji centered on a 128×64 black canvas. Returns PIL Image."""
    img = Image.new("L", (WIDTH, HEIGHT), color=BG)
    draw = ImageDraw.Draw(img)

    # Measure to center
    text_w, text_h = measure_text_multifont(kaomoji, fonts)

    # If text is wider than canvas, reduce font size and retry
    if text_w > WIDTH - 4:
        smaller = max(8, FONT_SIZE - 3)
        smaller_fonts = []
        for path in FONT_PATHS:
            try:
                smaller_fonts.append(ImageFont.truetype(path, smaller))
            except Exception:
                pass
        if smaller_fonts:
            text_w, text_h = measure_text_multifont(kaomoji, smaller_fonts)
            fonts_to_use = smaller_fonts
        else:
            fonts_to_use = fonts
    else:
        fonts_to_use = fonts

    x = max(0, (WIDTH - text_w) // 2)
    y = max(0, (HEIGHT - text_h) // 2)

    render_text_multifont(draw, kaomoji, fonts_to_use, x, y, fill=FG)
    return img


def save_as_1bit_png(img: Image.Image, path: Path) -> None:
    """Convert to 1-bit (pure black/white) and save as PNG."""
    # Threshold at 128 — anything above is white, below is black
    bw = img.point(lambda p: 255 if p > 128 else 0, "L")
    bw_1bit = bw.convert("1")
    bw_1bit.save(path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate Chotu OLED face PNGs")
    parser.add_argument("--preview", action="store_true", help="Open each image after generating")
    parser.add_argument("--out", type=str, default="faces", help="Output directory (default: ./faces)")
    parser.add_argument("--size", type=int, default=FONT_SIZE, help=f"Font size (default: {FONT_SIZE})")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)

    print(f"Output directory: {out_dir.resolve()}")
    print(f"Canvas: {WIDTH}×{HEIGHT}px, font size: {args.size}px\n")

    fonts = load_fonts(args.size)
    if not fonts:
        print("ERROR: No fonts loaded. Install NotoSansCJK or adjust FONT_PATHS.")
        sys.exit(1)

    print()
    failed = []

    for name, kaomoji in EXPRESSIONS.items():
        try:
            img = make_face_image(kaomoji, fonts)
            out_path = out_dir / f"{name}.png"
            save_as_1bit_png(img, out_path)
            size_bytes = out_path.stat().st_size
            print(f"  ✓ {name:15s}  {kaomoji}  →  {out_path.name} ({size_bytes}B)")

            if args.preview:
                # Scale up 3× for visibility on laptop screen
                preview = img.resize((WIDTH * 3, HEIGHT * 3), Image.NEAREST)
                preview.show(title=f"chotu: {name}")

        except Exception as e:
            print(f"  ✗ {name:15s}  FAILED: {e}")
            failed.append(name)

    print(f"\nDone. {len(EXPRESSIONS) - len(failed)}/{len(EXPRESSIONS)} generated in {out_dir.resolve()}")

    if failed:
        print(f"Failed: {', '.join(failed)}")
        print("Check font paths in FONT_PATHS at top of script.")

    print("\nNext step: copy faces/ to Pi alongside face.py")
    print("  scp -r faces/ chotu@chotu.local:~/chotu-bridge/chotu/faces/")


if __name__ == "__main__":
    main()
