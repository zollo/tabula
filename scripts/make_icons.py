#!/usr/bin/env python3
"""Draw the Tabula mark and write every icon size the frontend ships.

The mark: a faceted eye (keeping watch) whose iris is a speech bubble (the
posts it keeps), on a black-to-red rounded square. Same visual language as
Navi's compass-and-link icon.

    python scripts/make_icons.py        # needs Pillow (pip install pillow)

Writes into frontend/public/:
    favicon.svg, favicon.ico (16/32/48), apple-touch-icon.png, site.webmanifest,
    icons/favicon-32.png, icons/logo-256.png, icons/icon-192.png,
    icons/icon-512.png, icons/icon-maskable-512.png
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

PUBLIC = Path(__file__).resolve().parent.parent / "frontend" / "public"

# ── Palette ────────────────────────────────────────────────────────────────
# Background runs from near-black (bottom left) to Tabula red (top right).
GRADIENT = [(0.0, "#150508"), (0.5, "#870b20"), (1.0, "#ec2440")]
FACET_LIGHT = "#ffffff"
FACET_SHADE = "#f4c7ce"  # pale rose, the shaded half of each facet
STROKE = "#ffffff"
THEME_COLOR = "#0d0d0d"  # browser chrome; matches the app's black top bar
BACKGROUND_COLOR = "#0a0a0a"

# ── Geometry (512 × 512 design grid) ───────────────────────────────────────
S = 512
C = S / 2
TILE_INSET = 12  # transparent margin around the rounded square
TILE_RADIUS = 104
EYE_HALF_W = 184  # corners of the eye at C ± this
EYE_SAG = 112  # how far the lids rise above/below the centre line
IRIS_R = 66  # centre line of the speech-bubble ring
RING_W = 26
GAP_W = 14  # background-coloured gap around the ring
PUPIL_R = 24
TAIL_ANGLE = 135  # degrees, y down: bottom left
TAIL_HALF = 22  # half-width of the tail's opening, degrees
TAIL_LEN = 46  # how far the tail tip sits beyond the ring
MASKABLE_SCALE = 0.78  # glyph scale on full-bleed icons (80% safe zone)


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _lid_radius() -> float:
    return (EYE_HALF_W**2 + EYE_SAG**2) / (2 * EYE_SAG)


def eye_quadrants() -> list[tuple[str, list[tuple[float, float]]]]:
    """Four facets of the eye, alternating light and shade like a cut gem."""
    r = _lid_radius()
    n = 48

    def lid(sign: int) -> list[tuple[float, float]]:
        # sign=-1: upper lid (centre of its circle sits below), +1: lower lid
        cy = C - sign * (r - EYE_SAG)
        pts = []
        for i in range(n + 1):
            x = C - EYE_HALF_W + 2 * EYE_HALF_W * i / n
            dy = math.sqrt(max(r * r - (x - C) ** 2, 0))
            pts.append((x, cy + sign * dy))
        return pts

    upper, lower = lid(-1), lid(1)
    half = n // 2
    centre = (C, C)
    return [
        (FACET_LIGHT, upper[: half + 1] + [centre]),  # top left
        (FACET_SHADE, upper[half:] + [centre]),  # top right
        (FACET_LIGHT, lower[half:][::-1] + [centre]),  # bottom right
        (FACET_SHADE, lower[: half + 1][::-1] + [centre]),  # bottom left
    ]


def bubble_path() -> list[tuple[float, float]]:
    """Closed outline of the speech-bubble ring with its tail."""
    a0 = TAIL_ANGLE + TAIL_HALF
    a1 = TAIL_ANGLE - TAIL_HALF + 360
    pts = []
    steps = 64
    for i in range(steps + 1):
        a = math.radians(a0 + (a1 - a0) * i / steps)
        pts.append((C + IRIS_R * math.cos(a), C + IRIS_R * math.sin(a)))
    t = math.radians(TAIL_ANGLE)
    d = IRIS_R + TAIL_LEN
    pts.append((C + d * math.cos(t), C + d * math.sin(t)))
    return pts


# ── Raster ─────────────────────────────────────────────────────────────────
def _gradient(size: int) -> Image.Image:
    """Diagonal gradient, dark at bottom left, bright at top right."""
    stops = [(p, _hex(c)) for p, c in GRADIENT]
    line = Image.new("RGB", (256, 1))
    for i in range(256):
        t = i / 255
        for (p0, c0), (p1, c1) in zip(stops, stops[1:], strict=False):
            if p0 <= t <= p1:
                k = (t - p0) / (p1 - p0)
                rgb = tuple(round(a + (b - a) * k) for a, b in zip(c0, c1, strict=True))
                line.putpixel((i, 0), rgb)
                break
    # Stretch along x, then rotate 45° so it runs bottom-left → top-right.
    big = line.resize((size * 2, size * 2), Image.BILINEAR).rotate(45, resample=Image.BILINEAR)
    o = size // 2
    return big.crop((o, o, o + size, o + size))


def _stroke(draw: ImageDraw.ImageDraw, pts, width: float, fill) -> None:
    closed = list(pts) + [pts[0], pts[1]]
    draw.line(closed, fill=fill, width=round(width), joint="curve")
    for x, y in pts:  # round every corner, including the tail tip
        r = width / 2
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def render(size: int, *, full_bleed: bool = False) -> Image.Image:
    ss = 4  # supersample, then downscale for clean edges
    big = max(size, 512) * ss
    k = big / S
    scale = MASKABLE_SCALE if full_bleed else 1.0

    def tx(p):
        x, y = p
        return ((C + (x - C) * scale) * k, (C + (y - C) * scale) * k)

    bg = _gradient(big)
    out = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    tile = Image.new("L", (big, big), 0)
    if full_bleed:
        tile.paste(255, (0, 0, big, big))
    else:
        i = TILE_INSET * k
        box = (i, i, big - i, big - i)
        ImageDraw.Draw(tile).rounded_rectangle(box, radius=TILE_RADIUS * k, fill=255)
    out.paste(bg, (0, 0), tile)

    d = ImageDraw.Draw(out)
    for colour, poly in eye_quadrants():
        d.polygon([tx(p) for p in poly], fill=_hex(colour))

    bubble = [tx(p) for p in bubble_path()]
    sk = k * scale
    # Gap and iris are cut out with the background gradient itself.
    cut = Image.new("L", (big, big), 0)
    cd = ImageDraw.Draw(cut)
    _stroke(cd, bubble, (RING_W + 2 * GAP_W) * sk, 255)
    cd.polygon(bubble, fill=255)
    out.paste(bg, (0, 0), cut)

    _stroke(d, bubble, RING_W * sk, _hex(STROKE))
    cx, cy = tx((C, C))
    pr = PUPIL_R * sk
    d.ellipse((cx - pr, cy - pr, cx + pr, cy + pr), fill=_hex(STROKE))

    img = out.resize((size, size), Image.LANCZOS)
    return img.convert("RGB") if full_bleed else img


# ── Vector ─────────────────────────────────────────────────────────────────
def svg() -> str:
    f = lambda v: f"{v:.1f}".rstrip("0").rstrip(".")  # noqa: E731
    pts = lambda ps: " ".join(f"{f(x)},{f(y)}" for x, y in ps)  # noqa: E731
    stops = "".join(f'<stop offset="{p}" stop-color="{c}"/>' for p, c in GRADIENT)
    facets = "".join(f'<polygon fill="{c}" points="{pts(p)}"/>' for c, p in eye_quadrants())
    b = pts(bubble_path())
    i = TILE_INSET
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S} {S}">'
        '<defs><linearGradient id="g" gradientUnits="userSpaceOnUse" '
        f'x1="0" y1="{S}" x2="{S}" y2="0">{stops}'
        "</linearGradient></defs>"
        f'<rect x="{i}" y="{i}" width="{S - 2 * i}" height="{S - 2 * i}" '
        f'rx="{TILE_RADIUS}" fill="url(#g)"/>'
        f"{facets}"
        f'<polygon points="{b}" fill="url(#g)" stroke="url(#g)" '
        f'stroke-width="{RING_W + 2 * GAP_W}" stroke-linejoin="round"/>'
        f'<polygon points="{b}" fill="none" stroke="{STROKE}" '
        f'stroke-width="{RING_W}" stroke-linejoin="round"/>'
        f'<circle cx="{f(C)}" cy="{f(C)}" r="{PUPIL_R}" fill="{STROKE}"/>'
        "</svg>\n"
    )


def main() -> None:
    icons = PUBLIC / "icons"
    icons.mkdir(parents=True, exist_ok=True)

    (PUBLIC / "favicon.svg").write_text(svg())
    render(256).save(icons / "logo-256.png", optimize=True)
    render(32).save(icons / "favicon-32.png", optimize=True)
    render(192).save(icons / "icon-192.png", optimize=True)
    render(512).save(icons / "icon-512.png", optimize=True)
    render(512, full_bleed=True).save(icons / "icon-maskable-512.png", optimize=True)
    render(180, full_bleed=True).save(PUBLIC / "apple-touch-icon.png", optimize=True)
    render(256).save(PUBLIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])

    manifest = {
        "name": "Tabula",
        "short_name": "Tabula",
        "description": "Keep tabs on social media accounts: archive, search and export posts.",
        "start_url": "/",
        "display": "standalone",
        "background_color": BACKGROUND_COLOR,
        "theme_color": THEME_COLOR,
        "icons": [
            {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {
                "src": "/icons/icon-maskable-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }
    (PUBLIC / "site.webmanifest").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote icons to {PUBLIC}")


if __name__ == "__main__":
    main()
