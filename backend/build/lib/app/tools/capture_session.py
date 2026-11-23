"""Capture a signed-in browser session for Tabula's browser collection.

Run this on your own computer (it opens a visible browser window):

    pip install playwright==1.63.0 && playwright install chromium
    python capture_session.py x x-session.json        # or: reddit reddit-session.json

Sign in in the window that opens, then press Enter here. The cookies are saved
to the JSON file; upload it under Settings -> Browser sessions in Tabula.
Treat the file like a password: anyone with it is signed in as you.

Standalone on purpose: it needs only Playwright, not the rest of Tabula.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

LOGIN_URLS = {
    "x": "https://x.com/i/flow/login",
    "reddit": "https://www.reddit.com/login/",
}
LOGIN_COOKIES = {"x": "auth_token", "reddit": "reddit_session"}


async def capture(platform: str, out: str) -> int:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(locale="en-US")
        page = await context.new_page()
        await page.goto(LOGIN_URLS[platform])
        await asyncio.to_thread(
            input, f"Sign in to {platform} in the browser window, then press Enter here... "
        )
        state = await context.storage_state()
        await browser.close()
    names = {c["name"] for c in state.get("cookies", [])}
    if LOGIN_COOKIES[platform] not in names:
        print(f"Not signed in to {platform} yet (no {LOGIN_COOKIES[platform]} cookie); "
              "nothing saved.", file=sys.stderr)
        return 1
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    print(f"Saved {out}. Upload it in Tabula under Settings -> Browser sessions.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("platform", choices=sorted(LOGIN_URLS))
    parser.add_argument("out", help="where to write the session JSON")
    args = parser.parse_args()
    return asyncio.run(capture(args.platform, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
