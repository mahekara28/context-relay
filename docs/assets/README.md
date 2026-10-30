# README artwork

These original SVGs are part of Context Relay and use the repository's MIT license.
They contain editable text and vector shapes, without external images, fonts or scripts.

| File pair | Purpose |
| --- | --- |
| `relay-hero.svg` / `relay-hero-mobile.svg` | Project identity and the sender → packet → receiver story. |
| `relay-handoff.svg` / `relay-handoff-mobile.svg` | Explicit CSV-to-JSON supersession and handoff acceptance. |
| `relay-cli.svg` / `relay-cli-mobile.svg` | An illustrated, abbreviated CLI result from the task quickstart. |
| `relay-packet.svg` / `relay-packet-mobile.svg` | Fields carried by the task packet and its budget behavior. |
| `relay-integrations.svg` / `relay-integrations-mobile.svg` | The available connection paths into the local task service. |

Desktop layouts use a 1280-unit viewBox. Mobile layouts use 600 units and are
selected by the README's `<picture>` elements at viewport widths of 600px or less.
Each file includes a title and description; each README image also has alt text.
The fixed palettes remain legible on light and dark page backgrounds.

Edit both sizes when changing a feature or label. Keep essential information in
the surrounding Markdown too. Use the [task quickstart](../TASK_QUICKSTART.md)
to verify command examples and the [packet compiler](../../src/context_relay/task_packet.py)
to check packet fields. The terminal artwork abbreviates output, omits IDs and
fields, and does not depict a live model run or a completed reporting task.

Before publishing, render the README at desktop and phone widths, check for
clipped text, and confirm that the mobile sources still survive GitHub's
Markdown renderer. Avoid putting benchmark claims or unverified integrations
into the artwork.
