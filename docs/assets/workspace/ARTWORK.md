# Published beta artwork

The `hero*.svg` and `architecture*.svg` variants document the published
`0.3.0-beta.1` workspace. The current main README and website use actual
[development thread map captures](README.md) instead.

The banner shows the published npm beta, the actual `npx @mahekara28/context-relay@beta ui` command, Node requirement and bundled harness logos. The architecture shows native capture and MCP as separate paths into one local service, with SQLite and optional reviewed Nebius drafts. Lines connect components without decorative arrow glyphs.

## Regenerate

```sh
python3 scripts/npm/render-brand.py
```

The renderer reads the package name/version from `package.json`, embeds the [attributed harness logos](../../../packages/relay/ui/assets/THIRD_PARTY_LICENSE.txt), and uses system fonts without external requests. It generates editable SVGs with accessible titles/descriptions. The website uses the same warm neutral / coral palette; its social card is generated with `npm run site:social`.

Inspect all eight variants at their native size and the mobile versions at 320px. Essential installation commands and architecture details also appear as normal README text. [Actual workspace screenshots and GIF](README.md) are generated separately and remain evidence of the real product interface.
