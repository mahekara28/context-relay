# Contributing

The npm product lives in `packages/relay`; the Python research/API code has its own test suite and database. Keep their versioned formats separate.

## Local checks

```sh
npm ci
npm run typecheck
npm test
npm run test:package
npx playwright install chromium
npm run test:browser
```

Use `RELAY_CHROME=/absolute/path/to/chrome` to run browser checks with an installed Chromium executable. These checks use synthetic temporary projects; do not add private conversations or provider keys to fixtures. `npm run demo:render` captures the actual product for documentation; see the demo renderer for its isolated profile.

For Python changes, run the gates documented in [the Python reference](README.python.md) and the existing `alpha.yml` workflow.

## Changes and history

- Use small, coherent commits with descriptive messages. Preserve published PR history with follow-up commits.
- Use descriptive branches such as `feat/capture-filter` or `fix/source-freshness`.
- Include a regression case for changed provenance, trust, persistence, capture or transport behavior.
- Describe real coverage and remaining limits. Synthetic protocol tests are not live-harness certification; a receipt is not task verification.
- Keep version constants, package metadata, lockfile and release documentation consistent.
- Never commit `.env`, provider keys, workspace databases, native transcripts or local authentication URLs.

## Release PRs

Follow the [release series](docs/releases/NPM_RELEASE_PLAN.md). A reviewable release includes a packed npm artifact and passing final-head CI. Publishing a package is a distinct maintainer action. The workflow uploads a tarball artifact for review; it does not publish to npm.

## Third-party assets

Use existing vector logos with license/source attribution. Do not invent replacement harness logos or imply endorsement. README screenshots must come from the runnable product and label synthetic data.
