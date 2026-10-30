# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow==13.0.0", "playwright==1.64.1"]
# ///
"""Build the README artwork and replay the output of the real local example.

Documentation tooling only. Run with uv and optionally --browser /path/to/chrome.
The replay edits pacing; it does not simulate or measure model performance.
"""

from __future__ import annotations

import argparse
import ast
from html import escape
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import os
import tomllib

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
FRAME_MS = 100
FRAME_COUNT = 160
ACCENT = "#e65241"
THEMES = {
    "light": {
        "bg": "#ffffff",
        "fg": "#17191c",
        "muted": "#616872",
        "line": "#d9dde2",
        "panel": "#f6f7f8",
    },
    "dark": {
        "bg": "#0d1117",
        "fg": "#eff1f3",
        "muted": "#9ca5b1",
        "line": "#343b45",
        "panel": "#161b22",
    },
}


def text(x, y, value, size=20, fill="#17191c", weight=400, anchor="start", mono=False):
    family = (
        "Menlo, Consolas, monospace" if mono else "Helvetica Neue, Arial, sans-serif"
    )
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(str(value))}</text>'
    )


def svg(width, height, title, description, content):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">\n'
        f'<title id="title">{escape(title)}</title><desc id="desc">{escape(description)}</desc>\n'
        + "\n".join(content)
        + "\n</svg>\n"
    )


def banner(theme, mobile=False):
    c = THEMES[theme]
    w, h = (640, 250) if mobile else (1280, 300)
    x, y = (38, 68) if mobile else (64, 100)
    content = [
        f'<rect width="{w}" height="{h}" fill="{c["bg"]}"/>',
        f'<g transform="translate({x} {y})" fill="none" stroke="{ACCENT}" stroke-width="5" stroke-linecap="square">'
        '<path d="M0 0H27V27H54M0 12V54H27M54 0V42H27"/></g>',
        text(x + 82, y + 43, "context relay", 57 if mobile else 78, c["fg"], 500),
        text(
            40 if mobile else 149,
            171 if mobile else 192,
            "Task context across agent harnesses.",
            26 if mobile else 25,
            c["muted"],
        ),
    ]
    if not mobile:
        content += [
            f'<g fill="none" stroke="{c["line"]}" stroke-width="2">'
            '<path d="M972 110H1208M972 142H1208M972 174H1208"/></g>',
            f'<path d="M972 110H1048V142H1134V174H1208" fill="none" stroke="{ACCENT}" stroke-width="3"/>',
            f'<rect x="1127" y="135" width="14" height="14" fill="{ACCENT}"/>',
            text(
                1208,
                211,
                "STATE / TRANSFER / CONTINUE",
                12,
                c["muted"],
                anchor="end",
                mono=True,
            ),
        ]
    content += [
        f'<path d="M{x} {h - 65}H{w - x}" stroke="{c["line"]}"/>',
        text(
            x, h - 28, "PYTHON / CLI / MCP", 16 if mobile else 15, c["muted"], mono=True
        ),
        text(
            w - x,
            h - 28,
            "SOURCE PREVIEW",
            16 if mobile else 15,
            ACCENT,
            anchor="end",
            mono=True,
        ),
    ]
    return svg(
        w,
        h,
        "Context Relay",
        "Task context across agent harnesses. Python, CLI and optional MCP. Experimental source preview.",
        content,
    )


def capabilities(theme, tool_count, dependency_count, mobile=False):
    c = THEMES[theme]
    width, height = (640, 216) if mobile else (960, 108)
    content = [f'<rect width="{width}" height="{height}" fill="{c["bg"]}"/>']
    for index, (value, label) in enumerate(
        [
            (tool_count, "MCP tools"),
            (2, "project config generators"),
            (dependency_count, "core runtime dependencies"),
        ]
    ):
        if mobile:
            y = index * 64 + 46
            content.extend(
                [
                    text(62, y, value, 34, ACCENT, 500, "middle", True),
                    text(112, y, label, 25, c["muted"]),
                ]
            )
            continue
        x = index * 320 + 160
        if index:
            content.append(f'<path d="M{index * 320} 24V84" stroke="{c["line"]}"/>')
        content.extend(
            [
                text(x, 48, value, 35, ACCENT, 500, "middle", True),
                text(x, 80, label, 17, c["muted"], anchor="middle"),
            ]
        )
    return svg(
        width,
        height,
        "Available interfaces",
        f"{tool_count} MCP tools, two project configuration generators, {dependency_count} core runtime dependencies. Counts describe the current source.",
        content,
    )


def architecture(theme, mobile=False):
    c = THEMES[theme]
    w, h = (640, 655) if mobile else (1280, 340)
    content = [
        f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="6" fill="{c["bg"]}" stroke="{c["line"]}"/>'
    ]

    def box(x, y, bw, bh, title, subtitle):
        content.extend(
            [
                f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="4" fill="{c["panel"]}" stroke="{c["line"]}"/>',
                text(x + 20, y + 35, title, 24, c["fg"], 500),
                text(x + 20, y + 64, subtitle, 22 if mobile else 17, c["muted"]),
            ]
        )

    if mobile:
        content += [
            text(32, 48, "LOCAL TASK ARCHITECTURE", 18, c["muted"], mono=True),
            text(32, 100, "Claude Code · Codex CLI", 28, c["fg"], 500),
            text(32, 133, "Other MCP clients: manual setup", 23, c["muted"]),
        ]
        box(32, 184, 390, 86, "MCP / stdio", "Explicit reads and reports")
        box(32, 340, 390, 86, "Task service", "Revision and handoff checks")
        box(32, 496, 390, 86, "SQLite", "Local task state and history")
        content += [
            f'<path d="M224 145V184M224 270V340M224 426V496" fill="none" stroke="{ACCENT}" stroke-width="2"/>',
            f'<path d="M218 176L224 184L230 176M218 332L224 340L230 332M218 488L224 496L230 488" fill="none" stroke="{ACCENT}" stroke-width="2"/>',
            text(464, 299, "CLI /", 22, c["fg"], 500),
            text(464, 329, "Python", 22, c["fg"], 500),
            f'<path d="M516 345V383H422M430 377L422 383L430 389" fill="none" stroke="{c["muted"]}" stroke-width="2"/>',
            text(
                32,
                625,
                "The host controls identity and completion checks.",
                22,
                c["muted"],
            ),
        ]
    else:
        content += [
            text(40, 48, "LOCAL TASK ARCHITECTURE", 16, c["muted"], mono=True),
            text(40, 132, "Claude Code", 24, c["fg"], 500),
            text(40, 167, "Codex CLI", 24, c["fg"], 500),
            text(40, 202, "Other MCP clients", 22, c["muted"]),
            text(40, 230, "manual stdio setup", 16, c["muted"]),
        ]
        box(350, 124, 244, 92, "MCP / stdio", "Reads and reports")
        box(682, 124, 252, 92, "Task service", "Revisions and handoffs")
        box(1022, 124, 218, 92, "SQLite", "Local state + history")
        content += [
            f'<path d="M275 122V203M275 170H350M594 170H682M934 170H1022" fill="none" stroke="{ACCENT}" stroke-width="2"/>',
            f'<path d="M342 164L350 170L342 176M674 164L682 170L674 176M1014 164L1022 170L1014 176" fill="none" stroke="{ACCENT}" stroke-width="2"/>',
            text(808, 70, "CLI / Python", 22, c["fg"], 500, "middle"),
            f'<path d="M808 83V124M802 116L808 124L814 116" fill="none" stroke="{c["muted"]}" stroke-width="2"/>',
            f'<path d="M40 267H1240" stroke="{c["line"]}"/>',
            text(
                40,
                307,
                "The host controls identity, context injection and completion checks.",
                20,
                c["muted"],
            ),
        ]
    return svg(
        w,
        h,
        "Local task architecture",
        "Project-configured Claude Code and Codex CLI, or manually configured MCP clients, call the optional stdio server. MCP, CLI and Python access the task service and local SQLite. The host supplies identity and completion checks.",
        content,
    )


def capture_demo():
    result = subprocess.run(
        [sys.executable, str(ROOT / "examples/task_handoff_demo.py"), "--json"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def render_replay(browser, report, output, mobile=False):
    width, height = (640, 840) if mobile else (1200, 748)
    page = browser.new_page(
        viewport={"width": width, "height": height}, device_scale_factor=1
    )
    # The actual example supplies every result line. Only its display pacing is authored.
    lines = report["transcript"]
    payload = json.dumps(lines).replace("<", "\\u003c")
    page.set_content(
        """<!doctype html><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:#111418;color:#e8eaed;font-family:Menlo,Consolas,monospace}
.window{height:100vh;border:1px solid #343a43;border-radius:6px;overflow:hidden}
.bar{height:60px;border-bottom:1px solid #343a43;display:flex;align-items:center;justify-content:space-between;padding:0 38px;font-size:15px;color:#a0a8b5}
.bar strong{font-weight:400;color:#e8eaed}.indicator{display:inline-block;width:7px;height:7px;background:#e65241;margin-right:12px}
.terminal{padding:36px 48px;font-size:22px;line-height:34px}
.command{color:#f4f5f6;margin:0 0 24px;white-space:pre-wrap}.prompt{color:#e65241}
.line{white-space:pre;height:34px;color:#aeb7c4}.heading{color:#f0f2f4}.removed{color:#c49f99}.added,.accepted{color:#f0f2f4}.note{color:#8d98a7}
.footer{position:absolute;bottom:0;left:0;width:100%;border-top:1px solid #343a43;padding:16px 38px;font:14px Arial,sans-serif;color:#96a1af}
@media(max-width:700px){.bar{padding:0 24px;font-size:14px}.terminal{padding:32px 24px;font-size:26px;line-height:40px}.line{height:40px}.note{font-size:22px}.footer{font-size:16px;line-height:23px;padding:16px 24px}.command{font-size:22px;line-height:34px;margin-bottom:26px}}
</style><div class="window"><div class="bar"><strong><span class="indicator"></span>context-relay / local example</strong><span>Python</span></div><div class="terminal"><pre class="command"><span class="prompt">$ </span><span id="command"></span></pre><div id="output"></div></div><div class="footer">Replay of runnable example output · Playback timing edited for readability</div></div>"""
        + "<script>const lines="
        + payload
        + """;
const command = 'python examples/task_handoff_demo.py';
const output=document.getElementById('output');
for(const line of lines){const node=document.createElement('div');node.className='line '+line.kind;node.textContent=line.text||' ';output.appendChild(node)}
window.renderFrame=t=>{document.getElementById('command').textContent=command.slice(0,Math.min(command.length,Math.floor(t/1.5*command.length)))+(t<1.5?'▏':'');
const count=t<2?0:t<4.3?3:t<7.3?8:lines.length;
[...output.children].forEach((node,i)=>node.style.visibility=i<count?'visible':'hidden');};renderFrame(0);
</script>"""
    )

    def frame(t):
        page.evaluate("t=>renderFrame(t)", t)
        return Image.open(BytesIO(page.screenshot())).convert("RGB")

    suffix = "-mobile" if mobile else ""
    final = frame(12)
    final.save(output / f"demo{suffix}.png", optimize=True)
    palette = final.quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    frames = [
        frame(i * FRAME_MS / 1000).quantize(palette=palette, dither=Image.Dither.NONE)
        for i in range(FRAME_COUNT)
    ]
    path = output / f"demo{suffix}.gif"
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_MS,
        loop=0,
        disposal=1,
        optimize=False,
        comment=b"Replay of actual task_handoff_demo.py output. Display pacing edited; no model calls.",
    )
    with Image.open(path) as image:
        duration = 0
        for i in range(image.n_frames):
            image.seek(i)
            duration += image.info["duration"]
        if duration != FRAME_COUNT * FRAME_MS or image.info.get("loop") != 0:
            raise RuntimeError("Replay duration or looping metadata changed.")
        print(
            f"{path.name}: {image.n_frames} frames, {duration} ms, {path.stat().st_size:,} bytes",
            flush=True,
        )
    # Verify every output line remains inside the canvas, including phone layout.
    page.evaluate("renderFrame(12)")
    overflow = page.locator(".line,.command").evaluate_all(
        "(nodes)=>nodes.filter(n=>n.scrollWidth>n.clientWidth).map(n=>n.textContent)"
    )
    if overflow:
        raise RuntimeError(f"Transcript overflow: {overflow}")
    if (
        page.locator("#output").bounding_box()["y"]
        + page.locator("#output").bounding_box()["height"]
        > page.locator(".footer").bounding_box()["y"]
    ):
        raise RuntimeError("Transcript overlaps the footer.")
    page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Optional Chromium/Chrome executable")
    parser.add_argument("--output-dir", type=Path, default=ASSETS)
    args = parser.parse_args()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    report = capture_demo()
    (output / "demo-output.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    (output / "demo-transcript.txt").write_text(
        "$ python examples/task_handoff_demo.py\n\n"
        + "\n".join(line["text"] for line in report["transcript"])
        + "\n"
    )
    tree = ast.parse((ROOT / "src/context_relay/mcp_server.py").read_text())
    tool_count = sum(
        isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr == "tool"
        and isinstance(d.func.value, ast.Name)
        and d.func.value.id == "server"
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        for d in n.decorator_list
    )
    dependencies = len(
        tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    )
    for theme in THEMES:
        for mobile in [False, True]:
            suffix = f"-{theme}" + ("-mobile" if mobile else "")
            (output / f"banner{suffix}.svg").write_text(banner(theme, mobile))
            (output / f"architecture{suffix}.svg").write_text(
                architecture(theme, mobile)
            )
            (output / f"capabilities{suffix}.svg").write_text(
                capabilities(theme, tool_count, dependencies, mobile)
            )
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.browser, headless=True)
        try:
            render_replay(browser, report, output)
            render_replay(browser, report, output, mobile=True)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
