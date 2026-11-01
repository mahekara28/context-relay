# Context Relay website

Published at **https://mahekara28.github.io/context-relay/** with GitHub Pages.
The page uses the product's warm neutral / coral theme, local logos and actual
workspace captures. It is a static introduction, not the user's Relay service.
There are no analytics, third-party scripts, model requests or localhost calls.

## Build and preview

```sh
npm ci
npm run site:build
npm run site:preview
```

Open `http://127.0.0.1:4173/context-relay/`. The preview intentionally uses the
GitHub project subpath so broken relative links are visible before deployment.
`scripts/site/build.mjs` copies a fixed public asset allowlist into ignored
`_site/`; source, credentials, databases and tests are not deployment inputs.
Version placeholders come from `package.json`.

```sh
npx --no-install playwright install chromium
npm run test:site
```

Set `RELAY_CHROME` to an existing Chrome executable if needed. The browser checks
cover project-path assets, documentation links, install command version, keyboard
tour navigation, clipboard success and denial, reduced-motion controls, persistent
theme choice, both themes at 320/390/768/1024/1440px, and the nested 404 route.

To regenerate the social card from its HTML/CSS source:

```sh
npm run site:social
npm run site:build
```

`site/release.json` records the version currently published on npm. The checkout's `package.json` can be newer during development; the site must not label its unpublished version as an npm release. The hero, social card and tour show the current development thread map with an explicit preview label. The install commands still target the published npm beta.

The three thread map screenshots are captured from the real interface using
synthetic data. Rebuild them with `npm run build` and
`node scripts/npm/render-map.mjs`, then rebuild the social card and site.
See [capture instructions](../docs/assets/workspace/README.md).
Harness logos retain their [bundled attribution](../packages/relay/ui/assets/THIRD_PARTY_LICENSE.txt).

## Publish

Repository Settings → Pages → Source must be **GitHub Actions**. The
`pages.yml` workflow builds and tests pull requests without deploying them. A
matching push to `main` builds, tests and publishes `_site/` through the
`github-pages` environment. Manual workflow dispatch on `main` can redeploy.
Deployment uses GitHub's scoped workflow token and OIDC; no PAT is stored in the
repository or website. Workflow actions are pinned to immutable commits.

The About website field should remain `https://mahekara28.github.io/context-relay/`.
Installation offers global npm and npx commands. Keep both modes, the beta tag,
version pin and FAQ synchronized with verified npm releases.
Do not claim live model evaluation, automatic history capture through MCP, or
successful task completion from an acceptance receipt.
