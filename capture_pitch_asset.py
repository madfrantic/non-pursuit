"""
Pitch-deck screenshot of the running Non-Pursuit app.

Point it at a live Streamlit server (the app must already be running --
this script deliberately does not start one, so it captures exactly the
build you are demoing). It loads the page in headless Chromium, waits for
Streamlit to finish its first script run, injects presentation CSS, and
writes a 2x-resolution PNG.

Two things make this harder than a plain page.screenshot():

  * Streamlit scrolls *inside* <section data-testid="stMain">, not on the
    document. Playwright's full_page=True measures the document, so it
    captures one viewport and stops. The fix is to measure the inner
    scroll height and grow the viewport to match before shooting.
  * The app is not "loaded" when the DOM is ready -- the websocket run
    happens after. Waiting for the status widget ("Running...") to clear
    is the signal that the script finished.

Usage:
    python capture_pitch_asset.py
    python capture_pitch_asset.py --url http://localhost:8501 --out shot.png
"""
import argparse
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

DEFAULT_URL = "http://localhost:8501"
DEFAULT_OUT = "presentation_asset_v1.png"

# The app container gets treated as a card floating on a slide: the
# gradient is painted on the outermost element, the container is inset
# away from the edges, and the shadow does the rest. Everything is
# !important because Streamlit's own styles are injected later.
PRESENTATION_CSS = """
/* --- strip the developer chrome ------------------------------------ */
header,
header[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
[data-testid="manage-app-button"],
#MainMenu,
footer {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
}

/* --- the slide behind the app --------------------------------------- */
html, body, .stApp, [data-testid="stApp"] {
    background: radial-gradient(120% 120% at 15% 0%, #1c2a4d 0%, #0e1526 45%, #070b14 100%) !important;
}

/* --- the app as a card ---------------------------------------------- */
[data-testid="stAppViewContainer"] {
    inset: 44px !important;
    width: auto !important;
    height: auto !important;
    border-radius: 16px !important;
    overflow: hidden !important;
    border: 1px solid rgba(212, 175, 55, 0.28) !important;
    box-shadow:
        0 2px 4px rgba(0, 0, 0, 0.25),
        0 12px 28px rgba(0, 0, 0, 0.45),
        0 48px 96px -24px rgba(0, 0, 0, 0.75),
        0 0 0 1px rgba(255, 255, 255, 0.04) inset !important;
}

/* Streamlit pins the sidebar to the real viewport edge; without this it
   escapes the rounded corner and sits under the card. */
[data-testid="stSidebar"] {
    border-top-left-radius: 16px !important;
    border-bottom-left-radius: 16px !important;
}

/* The top of the main column sits flush against the card edge once the
   header is gone -- give it back some breathing room. */
[data-testid="stMain"] .block-container {
    padding-top: 2.25rem !important;
}

/* No scrollbars in a still image. */
* { scrollbar-width: none !important; }
*::-webkit-scrollbar { display: none !important; }
"""

# How much taller than the viewport the content is allowed to grow before
# we stop -- a runaway page should produce a big screenshot, not a
# gigabyte one.
MAX_HEIGHT = 9000

# Gap between the card and the slide edge; must match `inset` in the CSS.
CARD_INSET = 44

MEASURE_JS = """
() => {
    // How tall the viewport must be for nothing to be clipped.
    //
    // Neither obvious measurement works on its own:
    //   * "deepest element on the page" chases itself, because the
    //     sidebar is height:100vh and grows with every resize.
    //   * scrollHeight is clamped to clientHeight, so once the viewport
    //     is large enough it reports the viewport back to us and the
    //     overshoot can never be corrected.
    // What is stable is the content itself: the children of each scroll
    // container are sized by their content, not by the viewport.
    const bottoms = [];
    const contentBottom = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return;
        for (const child of el.children) {
            const r = child.getBoundingClientRect();
            if (r.height === 0) continue;
            bottoms.push(r.bottom + window.scrollY);
        }
    };
    contentBottom('[data-testid="stMain"]');
    contentBottom('[data-testid="stSidebarContent"]');

    if (bottoms.length === 0) {
        for (const el of document.querySelectorAll('body *')) {
            const cs = getComputedStyle(el);
            if (cs.position === 'fixed' || cs.display === 'none') continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            if (r.height > window.innerHeight * 0.9) continue;
            bottoms.push(r.bottom + window.scrollY);
        }
    }
    return bottoms.length ? Math.max(...bottoms) : document.body.scrollHeight;
}
"""


async def _wait_for_streamlit(page, timeout_ms):
    """Block until the first script run has actually finished rendering."""
    await page.wait_for_selector('[data-testid="stAppViewContainer"]',
                                 state="visible", timeout=timeout_ms)
    # The status widget only exists while a run is in flight, so its
    # absence -- not its presence -- is what we wait on.
    try:
        await page.wait_for_selector('[data-testid="stStatusWidget"]',
                                     state="detached", timeout=timeout_ms)
    except Exception:
        pass  # never appeared; the run was already done
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass  # the websocket keeps a connection open on some builds
    await page.wait_for_timeout(1200)  # fonts, images, final layout


async def capture(url: str, out_path: Path, width: int, scale: int,
                  timeout_ms: int) -> Path:
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--force-color-profile=srgb"])
        page = await (await browser.new_context(
            viewport={"width": width, "height": 1000},
            device_scale_factor=scale,
            color_scheme="dark",
        )).new_page()

        print(f"→ loading {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await _wait_for_streamlit(page, timeout_ms)

        print("→ injecting presentation CSS")
        await page.add_style_tag(content=PRESENTATION_CSS)
        await page.wait_for_timeout(600)

        # Resize until the measurement settles. The height is set
        # absolutely rather than only grown, so an overshoot on one pass
        # is corrected on the next.
        height = 1000
        for _ in range(6):
            measured = int(await page.evaluate(MEASURE_JS))
            target = min(measured + CARD_INSET, MAX_HEIGHT)
            if abs(target - height) <= 2:
                break
            height = target
            await page.set_viewport_size({"width": width, "height": height})
            await page.wait_for_timeout(700)
        print(f"→ canvas {width}x{height} @{scale}x = "
              f"{width * scale}x{height * scale}px")

        clipped = await page.evaluate("""() => {
            const r = [];
            for (const sel of ['[data-testid="stMain"]',
                               '[data-testid="stSidebarContent"]']) {
                const el = document.querySelector(sel);
                if (el && el.scrollHeight > el.clientHeight + 2)
                    r.push(`${sel} (${el.scrollHeight - el.clientHeight}px cut)`);
            }
            return r;
        }""")
        if clipped:
            print(f"! still clipped: {', '.join(clipped)}")

        await page.screenshot(path=str(out_path), full_page=True, type="png")
        await browser.close()
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--width", type=int, default=1600,
                        help="CSS pixel width of the capture (default: 1600)")
    parser.add_argument("--scale", type=int, default=2,
                        help="device pixel ratio; 2 = retina (default: 2)")
    parser.add_argument("--timeout", type=int, default=45000,
                        help="per-step timeout in ms (default: 45000)")
    args = parser.parse_args()

    out_path = Path(args.out).resolve()
    try:
        asyncio.run(capture(args.url, out_path, args.width, args.scale,
                            args.timeout))
    except Exception as exc:
        print(f"✗ capture failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"  is the app running at {args.url}?", file=sys.stderr)
        return 1

    size_kb = out_path.stat().st_size / 1024
    print(f"✓ {out_path} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
