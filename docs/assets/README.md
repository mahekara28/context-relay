# Project artwork

## Current npm README

The main README uses the warm neutral / coral artwork in [`workspace/`](workspace/ARTWORK.md), with light/dark and mobile layouts. Its banners show the public npm beta and a runnable npx command. Regenerate them with `python3 scripts/npm/render-brand.py`. The [workspace demo](workspace/README.md) is captured from the actual npm UI with synthetic data.

## Earlier Python artwork and demo

The files below belong to the earlier Python task-context presentation. They are retained as historical assets; the main README uses the npm artwork described above.

Original vector artwork and a replay of the runnable local task example.
The files use this repository's MIT license. No external images, fonts or
scripts are embedded. The layout takes inspiration from the concise banner
and prominent product demo in [AgentMemory](https://github.com/rohitg00/agentmemory);
its artwork and content are not reused.

## Visual system

A compact geometric mark, neutral surfaces, system typography and one red accent
(`#e65241`). Diagrams show the implemented call paths. The terminal is the main
product visual; decorative animation is omitted.

| Files | Purpose |
| --- | --- |
| `banner-{light,dark}[-mobile].svg` | Wordmark and project identity |
| `capabilities-{light,dark}[-mobile].svg` | Current interface counts, not performance or adoption metrics |
| `architecture-{light,dark}[-mobile].svg` | Local MCP, CLI and Python paths into task storage |
| `demo[-mobile].gif` | 16-second replay with a held final state |
| `demo[-mobile].png` | Final state for reduced motion and direct still-image links |
| `demo-output.json` / `demo-transcript.txt` | Checked example results and accessible transcript |

The original presentation selected light/dark artwork with `prefers-color-scheme`. At 600px
and below it uses separate mobile layouts. Reduced motion selects PNGs before
any animated source. SVGs have titles and descriptions; the README provides
alt text and the transcript. Essential facts also appear in normal Markdown.

## What the replay shows

Audience: developers deciding whether to try the source preview.
The example makes a sourced instruction change and transfers its current state
between two local principals. It does not invoke agent harnesses or models,
implement the report, or measure runtime performance.

| Claim | Evidence | Presentation |
| --- | --- | --- |
| JSON explicitly supersedes CSV at revision 2. | [Runnable example](../../examples/task_handoff_demo.py), [task service](../../src/context_relay/task_service.py) | The actual old and current instruction texts are printed. |
| An offer can be accepted by its named recipient. | [Handoff service](../../src/context_relay/handoffs.py) | The example checks acceptance and prints the returned revision and status. |
| Acceptance does not establish completion. | [Task diagnostics](../../src/context_relay/task_service.py) | The example checks that verification is absent and prints `not recorded`. |
| The server exposes eight tools. | [MCP server](../../src/context_relay/mcp_server.py) | The renderer counts tool decorators from source. |
| Two project configuration generators exist; core has zero runtime dependencies. | [Connectors](../../src/context_relay/connectors.py), [package metadata](../../pyproject.toml) | Generator count is authored; dependency count is read from metadata. |

The renderer runs the example with `--json` and uses its transcript unchanged.
Random IDs, timestamps and local paths are deliberately excluded by the example.
Only command typing and output reveal timing are edited for readability. The
caption states this in both the image and the surrounding README.

## Regenerate

From the repository root, with Python 3.11+ and `uv`:

```sh
uv run --no-project --with playwright==1.64.1 playwright install chromium
uv run --script scripts/render_readme_animation.py
```

Pass `--browser /path/to/chrome` to use an installed Chromium browser, or
`--output-dir /tmp/relay-artwork` for a separate export. Pillow and Playwright
versions are pinned in the script; neither is a core runtime dependency.
Fonts follow the rendering machine's available system fonts.

The renderer generates every SVG and runs the local example before rendering
the replay. It uses a shared palette and merges consecutive unchanged GIF
frames while preserving duration. Both GIFs are under 55 KB with the current fonts. Regeneration validates replay duration, looping and transcript overflow.

After edits, inspect light and dark rendering at desktop, 768px and 320px widths.
Check the first frame, instruction change, acceptance, final hold and loop
boundary. Verify the README's source selection with reduced motion on and off,
and confirm GitHub preserves the `<picture>` sources. Keep the counts and
architecture aligned with source, and avoid claims about untested integrations.
