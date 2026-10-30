"""Render the npm README's editable SVGs using bundled, attributed logos."""
from html import escape
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/assets/workspace"
PACKAGE = json.loads((ROOT / "package.json").read_text())
COMMAND = f"npx {PACKAGE['name']}@beta ui"
FONT = "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Consolas, monospace"
HARNESSES = [("claude", "Claude Code"), ("codex", "Codex"),
             ("opencode", "OpenCode"), ("goose", "Goose")]
PALETTES = {
    "light": dict(paper="#f6f5f1", panel="#ffffff", ink="#222827",
                  muted="#626a65", line="#dcded7", accent="#bc3c23",
                  soft="#faeae3", terminal="#222827", command="#f3f4ed"),
    "dark": dict(paper="#171f1a", panel="#212a24", ink="#edf0e8",
                 muted="#b6c0b8", line="#3a453d", accent="#ff9d7d",
                 soft="#352d26", terminal="#111813", command="#edf0e8"),
}


class Artwork:
    def __init__(self, width, height, theme, title, description):
        self.colors = PALETTES[theme]
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">',
            f'<title id="title">{escape(title)}</title>',
            f'<desc id="description">{escape(description)}</desc>',
        ]
        self.rect(1, 1, width - 2, height - 2, "paper", radius=16)

    def text(self, x, y, value, size=22, color="ink", weight=400, mono=False):
        self.parts.append(
            f'<text x="{x}" y="{y}" font-family="{MONO if mono else FONT}" '
            f'font-size="{size}" font-weight="{weight}" fill="{self.colors[color]}">'
            f'{escape(value)}</text>'
        )

    def rect(self, x, y, width, height, color="panel", radius=8, stroke="line"):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" '
            f'fill="{self.colors[color]}" stroke="{self.colors[stroke]}"/>'
        )

    def logo(self, name, x, y, size=32):
        svg = (ROOT / f"packages/relay/ui/assets/{name}.svg").read_text()
        svg = re.sub(
            r'<svg\b[^>]*>',
            lambda match: re.sub(r'\s(?:width|height)="[^"]*"', '', match.group()).replace(
                '<svg', f'<svg x="{x}" y="{y}" width="{size}" height="{size}"'
            ), svg, count=1,
        )
        self.parts.append(f'<g color="{self.colors["ink"]}">{svg}</g>')

    def path(self, value):
        self.parts.append(
            f'<path d="{value}" fill="none" stroke="{self.colors["line"]}" stroke-width="2"/>'
        )

    def save(self, name):
        (OUTPUT / name).write_text("\n".join([*self.parts, '</svg>']) + "\n")


def hero(theme, mobile):
    art = Artwork(640 if mobile else 1200, 770 if mobile else 480, theme,
                  "Context Relay — keep the work, change the thread",
                  f"Public npm beta {PACKAGE['version']}. Local CLI, workspace and MCP. "
                  f"Try {COMMAND}. Node 22.16 or newer. Claude Code, Codex, OpenCode and Goose.")
    x = 40 if mobile else 48
    art.logo("relay", x, 38, 36)
    art.text(x + 50, 64, "Context Relay", 25, weight=600)
    if mobile:
        art.text(x, 119, f"PUBLIC NPM BETA · {PACKAGE['version']}", 20, "accent", 500)
        art.text(x, 207, "Keep the work.", 63, weight=600)
        art.text(x, 280, "Change the thread.", 63, "accent", 600)
        art.text(x, 335, "Reviewed context for your next", 27, "muted")
        art.text(x, 372, "coding session. CLI · workspace · MCP.", 27, "muted")
        for i, (name, label) in enumerate(HARNESSES):
            left, top = x + (i % 2) * 288, 411 + (i // 2) * 82
            art.rect(left, top, 272, 66)
            art.logo(name, left + 18, top + 19, 28)
            art.text(left + 60, top + 42, label, 23, weight=500)
        art.text(x, 605, "TRY IT WITH NPX", 20, "muted", 500)
        art.rect(x, 627, 560, 69, "terminal", stroke="terminal")
        art.text(x + 18, 670, COMMAND, 22, "command", mono=True)
        art.text(x, 739, "Node 22.16+ · Local by default · MIT", 22, "muted")
    else:
        art.text(848, 63, f"PUBLIC NPM BETA · {PACKAGE['version']}", 17, "accent", 500)
        art.text(x, 166, "Keep the work.", 60, weight=600)
        art.text(x, 235, "Change the thread.", 60, "accent", 600)
        art.text(x, 290, "Reviewed context for your next coding session.", 25, "muted")
        for i, (name, label) in enumerate(HARNESSES):
            left, top = 750 + (i % 2) * 210, 118 + (i // 2) * 103
            art.rect(left, top, 192, 86)
            art.logo(name, left + 18, top + 16, 28)
            art.text(left + 18, top + 67, label, 20, weight=500)
        art.rect(x, 344, 1104, 72, "terminal", stroke="terminal")
        art.text(x + 22, 389, COMMAND, 25, "command", mono=True)
        art.text(880, 388, "TRY IT WITH NPX", 17, "command", 500)
        art.text(x, 454, "LOCAL CLI · WORKSPACE · MCP", 16, "muted", 500)
        art.text(808, 454, "Node 22.16+ · Local by default · MIT", 17, "muted")
    return art


def architecture(theme, mobile):
    art = Artwork(640 if mobile else 1200, 1110 if mobile else 730, theme,
                  "Context Relay — one workspace, two integration paths",
                  "The npm package runs locally. Native capture adapters and MCP tools connect "
                  "four harnesses to one context service. SQLite stores reviewed state. "
                  "Optional Nebius proposals require approval and review.")
    if mobile:
        art.text(40, 49, "ONE WORKSPACE. TWO INTEGRATION PATHS.", 23, "muted", 500)
        for i, (name, label) in enumerate(HARNESSES):
            x, y = 40 + (i % 2) * 288, 82 + (i // 2) * 86
            art.rect(x, y, 272, 68)
            art.logo(name, x + 18, y + 20, 28)
            art.text(x + 61, y + 43, label, 23, weight=500)
        for x, label, title, lines in [
            (40, "NATIVE HISTORY", "Capture adapters", ["JSONL · app-server", "Local API · JSON export"]),
            (328, "CONTEXT ACCESS", "MCP tools", ["Inspect · retrieve", "Accept a handoff"]),
        ]:
            art.text(x, 295, label, 20, "muted", 500)
            art.rect(x, 316, 272, 150)
            art.text(x + 18, 358, title, 25, weight=600)
            for i, line in enumerate(lines):
                art.text(x + 18, 400 + i * 30, line, 21, "muted")
        art.path("M176 466v28h288v-28M320 494v34")
        art.rect(40, 528, 560, 171, "soft", stroke="accent")
        art.logo("relay", 64, 556, 34)
        art.text(114, 583, "Context Relay · local service", 28, weight=600)
        art.text(64, 628, "npm package · CLI · workspace · MCP", 24, "muted")
        art.text(64, 667, "Review · checkpoint · handoff", 24, "accent")
        art.path("M320 699v23H24v270h16M24 812h16")
        art.rect(40, 745, 560, 134)
        art.text(64, 790, "Private SQLite state", 28, weight=600)
        art.text(64, 831, "Sources · revisions · file hashes · receipts", 23, "muted")
        art.rect(40, 919, 560, 146)
        art.text(64, 964, "Optional Nebius Token Factory", 28, weight=600)
        art.text(64, 1005, "Selected evidence · validated draft", 23, "muted")
        art.text(64, 1038, "Your approval and review", 23, "muted")
    else:
        art.text(44, 49, "ONE WORKSPACE. TWO INTEGRATION PATHS.", 20, "muted", 500)
        for i, (name, label) in enumerate(HARNESSES):
            x = 44 + i * 282
            art.rect(x, 80, 266, 98)
            art.logo(name, x + 24, 109, 34)
            art.text(x + 77, 133, label, 23, weight=500)
        art.text(44, 222, "NATIVE HISTORY", 18, "muted", 500)
        art.text(616, 222, "CONTEXT ACCESS", 18, "muted", 500)
        art.rect(44, 244, 540, 106)
        art.rect(616, 244, 540, 106)
        art.text(70, 282, "Capture adapters", 25, weight=600)
        art.text(70, 318, "JSONL · app-server · local API · JSON exports", 19, "muted")
        art.text(642, 282, "MCP tools + resources", 25, weight=600)
        art.text(642, 318, "Inspect sources · retrieve packet · accept handoff", 19, "muted")
        art.path("M314 350v30h572v-30M600 380v27")
        art.rect(44, 408, 1112, 125, "soft", stroke="accent")
        art.logo("relay", 74, 445, 45)
        art.text(137, 458, "Context Relay · local service", 29, weight=600)
        art.text(137, 496, "npm package · CLI · workspace · MCP · reviewed handoffs", 23, "muted")
        art.path("M600 533v30M314 583v-20h572v20")
        art.rect(44, 584, 540, 106)
        art.rect(616, 584, 540, 106)
        art.text(70, 623, "Private SQLite state", 24, weight=600)
        art.text(70, 658, "Sources · revisions · file hashes · delivery receipts", 18, "muted")
        art.text(642, 623, "Optional Nebius Token Factory", 24, weight=600)
        art.text(642, 658, "Selected evidence · validated draft · your review", 18, "muted")
    return art


if __name__ == "__main__":
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for theme in PALETTES:
        for mobile in (False, True):
            suffix = ("-dark" if theme == "dark" else "") + ("-mobile" if mobile else "")
            hero(theme, mobile).save(f"hero{suffix}.svg")
            architecture(theme, mobile).save(f"architecture{suffix}.svg")
    print("Rendered eight npm README SVGs: light/dark, desktop/mobile.")
