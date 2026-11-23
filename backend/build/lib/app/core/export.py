"""Exporting an account's archived posts as JSON or PDF."""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from app.models.platform import PLATFORM_LABELS
from app.models.post import Post
from app.models.social_account import SocialAccount

# DejaVu covers far more than the PDF core fonts' Latin-1. The backend image
# installs fonts-dejavu-core; elsewhere we fall back to Helvetica.
_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu")
_LATIN1 = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         "•": "-", "…": "...", " ": " ", "→": "->"})
# Tabula red, for headings and rules in the PDF.
_RED = (200, 16, 46)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def account_dict(account: SocialAccount) -> dict[str, Any]:
    return {
        "platform": account.platform.value,
        "handle": account.handle,
        "display_name": account.display_name,
        "external_id": account.external_id,
        "profile_url": account.profile_url,
    }


def post_dict(post: Post) -> dict[str, Any]:
    return {
        "id": post.external_id,
        "kind": post.kind.value,
        "posted_at": _iso(post.posted_at),
        "url": post.url,
        "text": post.text,
        "media": post.media,
        "metrics": post.metrics,
        "fetched_at": _iso(post.fetched_at),
    }


def to_json(account: SocialAccount, posts: list[Post]) -> dict[str, Any]:
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "account": account_dict(account),
        "count": len(posts),
        "posts": [post_dict(p) for p in posts],
    }


def _find_font() -> tuple[str, str] | None:
    for d in _FONT_DIRS:
        regular, bold = os.path.join(d, "DejaVuSans.ttf"), os.path.join(d, "DejaVuSans-Bold.ttf")
        if os.path.exists(regular) and os.path.exists(bold):
            return regular, bold
    return None


def to_pdf(account: SocialAccount, posts: list[Post]) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="letter")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(True, margin=16)
    pdf.add_page()

    font = _find_font()
    if font:
        family = "DejaVu"
        for style, path in (("", font[0]), ("B", font[1])):
            pdf.add_font(family, style, path)

        def clean(text: str) -> str:
            # Drop characters outside the BMP (emoji); DejaVu has no glyphs for them.
            return "".join(c for c in text if ord(c) <= 0xFFFF)
    else:
        family = "Helvetica"

        def clean(text: str) -> str:
            return text.translate(_LATIN1).encode("latin-1", "replace").decode("latin-1")

    width = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_text_color(*_RED)
    pdf.set_font(family, "B", 18)
    pdf.multi_cell(width, 9, clean(account.display_name or account.handle))
    pdf.set_text_color(90, 90, 90)
    pdf.set_font(family, "", 10)
    label = PLATFORM_LABELS[account.platform]
    pdf.multi_cell(
        width,
        5,
        clean(
            f"{label} · @{account.handle} · {len(posts)} posts · exported "
            f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC"
        ),
    )
    pdf.set_draw_color(*_RED)
    pdf.line(pdf.l_margin, pdf.get_y() + 2, pdf.w - pdf.r_margin, pdf.get_y() + 2)
    pdf.ln(6)

    for post in posts:
        posted = post.posted_at if post.posted_at.tzinfo else post.posted_at.replace(tzinfo=UTC)
        pdf.set_text_color(90, 90, 90)
        pdf.set_font(family, "B", 9)
        head = f"{posted:%Y-%m-%d %H:%M} UTC · {post.kind.value}"
        pdf.multi_cell(width, 5, clean(head))
        pdf.set_text_color(20, 20, 20)
        pdf.set_font(family, "", 10.5)
        pdf.multi_cell(width, 5.2, clean(post.text or "(no text)"))
        extras = [f"{k}: {v}" for k, v in (post.metrics or {}).items() if v not in (None, "")]
        if post.media:
            extras.append(f"{len(post.media)} media")
        if extras or post.url:
            pdf.set_text_color(120, 120, 120)
            pdf.set_font(family, "", 8.5)
            if extras:
                pdf.multi_cell(width, 4.5, clean(" · ".join(extras)))
            if post.url:
                pdf.multi_cell(width, 4.5, clean(post.url), link=post.url)
        pdf.ln(2)
        pdf.set_draw_color(220, 220, 220)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(3)
    return bytes(pdf.output())
