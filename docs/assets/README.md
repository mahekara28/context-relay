# README artwork

This original artwork is part of Context Relay and uses the repository's MIT license.
The SVG sources contain editable text and vector shapes, without external images,
fonts or scripts. The banner also has generated GIF versions.

| File pair | Purpose |
| --- | --- |
| `relay-hero.svg` / `relay-hero-mobile.svg` | Project identity and the sender → packet → receiver story. |
| `relay-hero.gif` / `relay-hero-mobile.gif` | A 12-second illustrated loop of the instruction change, offer and acceptance. |
| `relay-handoff.svg` / `relay-handoff-mobile.svg` | Explicit CSV-to-JSON supersession and handoff acceptance. |
| `relay-cli.svg` / `relay-cli-mobile.svg` | An illustrated, abbreviated CLI result from the task quickstart. |
| `relay-packet.svg` / `relay-packet-mobile.svg` | Fields carried by the task packet and its budget behavior. |
| `relay-integrations.svg` / `relay-integrations-mobile.svg` | The available connection paths into the local task service. |

Desktop layouts use a 1280-unit viewBox. Mobile layouts use 600 units and are
selected by the README's `<picture>` elements at viewport widths of 600px or less.
Each file includes a title and description; each README image also has alt text.
The fixed palettes remain legible on light and dark page backgrounds.

## Animated banner

The banner is for developers discovering the project. It shows how an explicit
instruction change reaches another harness through a versioned task snapshot.
It illustrates the documented local handoff, not a live agent recording or a
performance benchmark. Its claims follow the implementation and reproducible
quickstart; its timing is visibly labeled as illustrative.

| Claim | Evidence | Visual consequence |
| --- | --- | --- |
| The JSON instruction explicitly supersedes CSV at revision 2. | [Task quickstart](../TASK_QUICKSTART.md) | The sender changes first; the packet changes after the update reaches it. |
| Packets carry active instructions and source references. | [Packet compiler](../../src/context_relay/task_packet.py) | The packet shows `Use JSON` and `message-2`. |
| Offers freeze a revision; acceptance checks the recipient and revision. | [Handoff service](../../src/context_relay/handoffs.py) | The offer appears before receiver checks and acceptance. |
| Accepting context does not verify task completion. | [Task quickstart](../TASK_QUICKSTART.md) | The final caption leaves completion unchecked. |

Lime marks task context; lilac identifies the two harnesses. A moving square
marks an active transfer. The final state holds before replay. The README
selects a static SVG when `prefers-reduced-motion: reduce` is set and also
offers a direct still-image link.

Regenerate both GIFs with the documentation-only renderer:

```sh
uv run --no-project --with playwright==1.64.1 playwright install chromium
uv run --script scripts/render_readme_animation.py
```

Alternatively, pass `--browser /path/to/chrome` to use an installed Chromium
browser. The renderer reads the existing SVGs and uses a single deterministic
timeline. Its Pillow and Playwright dependencies are pinned in the script;
they are not dependencies of Context Relay. Fonts follow the rendering browser.
Consecutive unchanged frames are merged while retaining their display duration.
Use `--output-dir /tmp/relay-animation` for an export outside the repository.

After a change, inspect the original instruction, correction, saved revision,
offer, checks, accepted state and loop boundary. Verify GIF loading and still
selection on GitHub at desktop, 768px and 320px widths, with reduced motion
enabled and disabled. The GIF has no playback controls; the still version is
the non-animated reading path.

## Editing the diagrams

Edit both sizes when changing a feature or label. Keep essential information in
the surrounding Markdown too. Use the [task quickstart](../TASK_QUICKSTART.md)
to verify command examples and the [packet compiler](../../src/context_relay/task_packet.py)
to check packet fields. The terminal artwork abbreviates output, omits IDs and
fields, and does not depict a live model run or a completed reporting task.

Before publishing, render the README at desktop and phone widths, check for
clipped text, and confirm that the mobile sources still survive GitHub's
Markdown renderer. Avoid putting benchmark claims or unverified integrations
into the artwork.
