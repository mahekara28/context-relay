# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow==13.0.0", "playwright==1.64.1"]
# ///
"""Render the README's authored handoff story as two looping GIFs.

Run with uv; pass --browser to use an installed Chromium executable, or install
Playwright's Chromium first. This is documentation tooling, not a runtime dependency.
The timeline is illustrative, not a recording or a measurement of a live agent.
"""

from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
NS = "{http://www.w3.org/2000/svg}"
FRAME_MS = 80
FRAME_COUNT = 150  # 12 seconds, including a readable final hold.
ET.register_namespace("", NS[1:-1])


def prepare_svg(mobile: bool) -> tuple[str, int, int]:
    """Reuse the still artwork so branding and geometry have one source."""
    suffix = "-mobile" if mobile else ""
    root = ET.parse(ASSETS / f"relay-hero{suffix}.svg").getroot()
    width, height = (int(root.attrib[key]) for key in ("width", "height"))

    def label(original: str, identifier: str) -> ET.Element:
        matches = [node for node in root.iter() if node.tag in {NS + "text", NS + "tspan"}
                   and node.text == original]
        if len(matches) != 1:
            raise ValueError(f"Expected one artwork label: {original!r}")
        matches[0].set("id", identifier)
        return matches[0]

    label("“Actually, use JSON.”", "sender-message")
    label("Continue from this state.", "receiver-message")
    label("Task context / revision 2" if mobile else "TASK CONTEXT / REVISION 2", "packet-title")
    if mobile:
        node = next(node for node in root.iter() if node.get("id") == "packet-title")
        node.set("y", "557")
        node.set("font-size", "19")
        body = ET.SubElement(root, NS + "text", {
            "id": "packet-body", "x": "60", "y": "589", "font-size": "26",
            "fill": "#16251f", "font-weight": "700",
            "font-family": "Arial, Helvetica, sans-serif",
        })
        body.text = "Use JSON · message-2"
    else:
        label("Goal + sources + next steps", "packet-body")

    # Name the receiver box so acceptance can be shown without implying task completion.
    x, y = ("40", "637") if mobile else ("798", "430")
    receiver = [node for node in root.iter(NS + "rect") if node.get("x") == x and node.get("y") == y]
    if len(receiver) != 1:
        raise ValueError("Receiver geometry changed; update the animation layout.")
    receiver[0].set("id", "receiver-box")

    # Retain muted structural arrows; the moving square identifies the active transfer.
    for node in root.iter(NS + "path"):
        if node.get("stroke") == "#d0f28e" and (not mobile or node.get("d", "").startswith("M300")):
            node.set("stroke", "#64794c")
    paths = ("M300 503V532", "M300 608V637") if mobile else (
        "M740 231H692V346H726", "M1128 346H1200V459H1166",
    )
    for identifier, path in zip(("input-path", "output-path"), paths):
        ET.SubElement(root, NS + "path", {
            "id": identifier, "d": path, "fill": "none", "stroke": "none",
        })
    ET.SubElement(root, NS + "rect", {
        "id": "transfer", "width": "12", "height": "12", "rx": "3",
        "fill": "#f6f4eb", "stroke": "#d0f28e", "stroke-width": "2",
        "visibility": "hidden",
    })

    # Replace the static footer with a stage caption and an explicit illustration label.
    ET.SubElement(root, NS + "rect", {
        "x": "32" if mobile else "48", "y": "722" if mobile else "551",
        "width": "536" if mobile else "1184", "height": "75" if mobile else "55",
        "fill": "#16251f",
    })
    footer = ET.SubElement(root, NS + "g", {"font-family": "Arial, Helvetica, sans-serif"})
    ET.SubElement(footer, NS + "text", {
        "id": "step-caption", "x": "40" if mobile else "64", "y": "751" if mobile else "586",
        "font-size": "24" if mobile else "18", "fill": "#dbe4da",
    })
    note = ET.SubElement(footer, NS + "text", {
        "x": "40" if mobile else "1216", "y": "783" if mobile else "586",
        "font-size": "20" if mobile else "16", "fill": "#aabcb0",
        "text-anchor": "start" if mobile else "end",
    })
    note.text = "ILLUSTRATED LOOP · TIMING IS ILLUSTRATIVE"
    return ET.tostring(root, encoding="unicode"), width, height


TIMELINE = r"""
const mobile = MOBILE;
const $ = id => document.getElementById(id);
const correction = '“Actually, use JSON.”';
window.renderFrame = t => {
  // One deterministic clock: instruction update precedes offer and acceptance.
  const revised = t >= 4.8, offered = t >= 6.4, checking = t >= 7.6, accepted = t >= 8.8;
  let message = '“Use CSV.”';
  if (t >= 1.6) {
    const progress = Math.min(1, (t - 1.6) / 1.6);
    message = correction.slice(0, Math.floor(progress * correction.length)) + (progress < 1 ? '▏' : '');
  }
  $('sender-message').textContent = message;
  $('packet-title').textContent = offered ? 'HANDOFF OFFER / REVISION 2'
    : `TASK CONTEXT / REVISION ${revised ? 2 : 1}`;
  $('packet-body').textContent = revised ? 'Use JSON · message-2' : 'Use CSV · message-1';
  $('receiver-message').textContent = accepted ? 'Use JSON · context accepted'
    : checking ? 'Check recipient + revision' : 'Waiting for a handoff';
  $('receiver-box').setAttribute('stroke', accepted ? '#d0f28e' : '#526353');
  $('receiver-box').setAttribute('stroke-width', accepted ? '2' : '1');
  let caption = mobile ? '1 / Original instruction: CSV' : '1 / Record the original CSV instruction and its source.';
  if (t >= 1.6) caption = mobile ? '2 / User correction: JSON' : '2 / The user changes the instruction from CSV to JSON.';
  if (t >= 3.6) caption = mobile ? '3 / Save revision 2' : '3 / Supersede CSV and save the JSON instruction at revision 2.';
  if (offered) caption = mobile ? '4 / Offer the current revision' : '4 / Offer an immutable snapshot of revision 2 to the receiver.';
  if (checking) caption = mobile ? '5 / Check recipient + revision' : '5 / The receiver checks the recipient and revision before acceptance.';
  if (accepted) caption = mobile ? 'Context ready. Completion unchecked.' : 'Context accepted. Task completion still needs a separate check.';
  $('step-caption').textContent = caption;
  const start = t >= 6.4 ? 6.4 : 3.6;
  const moving = (t >= 3.6 && t < 4.8) || (t >= 6.4 && t < 7.6);
  const token = $('transfer');
  token.setAttribute('visibility', moving ? 'visible' : 'hidden');
  if (moving) {
    const path = $(start === 3.6 ? 'input-path' : 'output-path');
    const u = (t - start) / 1.2;
    const eased = u * u * (3 - 2 * u);
    const point = path.getPointAtLength(path.getTotalLength() * eased);
    token.setAttribute('x', point.x - 6);
    token.setAttribute('y', point.y - 6);
  }
};
window.renderFrame(0);
"""


def render(browser, mobile: bool, output: Path) -> None:
    svg, width, height = prepare_svg(mobile)
    script = TIMELINE.replace("MOBILE", json.dumps(mobile))
    page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
    page.set_content('<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#16251f}svg{display:block}</style>'
                     + svg + "<script>" + script + "</script>")

    def frame(t: float) -> Image.Image:
        page.evaluate("t => window.renderFrame(t)", t)
        return Image.open(BytesIO(page.screenshot())).convert("RGB")

    # A shared palette prevents stationary text from shimmering between frames.
    samples = [frame(t) for t in (0, 2.4, 4.8, 7.6, 9.6)]
    atlas = Image.new("RGB", (width, height * len(samples)))
    for i, sample in enumerate(samples):
        atlas.paste(sample, (0, i * height))
    palette = atlas.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    frames = [frame(i * FRAME_MS / 1000).quantize(palette=palette, dither=Image.Dither.NONE)
              for i in range(FRAME_COUNT)]
    page.close()
    name = "relay-hero-mobile.gif" if mobile else "relay-hero.gif"
    destination = output / name
    frames[0].save(destination, save_all=True, append_images=frames[1:],
                   duration=FRAME_MS, loop=0, disposal=1, optimize=False,
                   comment=b"Illustrated Context Relay handoff. Timing is illustrative; no live model run.")
    with Image.open(destination) as result:
        duration = 0
        for index in range(result.n_frames):
            result.seek(index)
            duration += result.info["duration"]
        if duration != FRAME_COUNT * FRAME_MS or result.info.get("loop") != 0:
            raise RuntimeError("GIF export changed the intended duration or looping behavior.")
        print(f"{name}: {width}x{height}, {result.n_frames} encoded frames, {duration}ms, "
              f"{destination.stat().st_size:,} bytes", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Optional path to an installed Chromium or Chrome executable")
    parser.add_argument("--output-dir", type=Path, default=ASSETS)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.browser, headless=True)
        try:
            render(browser, False, args.output_dir)
            render(browser, True, args.output_dir)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
