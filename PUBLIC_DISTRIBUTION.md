# Public distribution

## Current source and wheel: 0.22.0a2

The current source includes the harness evaluation API and fixes mixed-message
memory, scoped history import and interrupted-action recovery. The `0.22.0a2`
wheel is built from this source; its SHA-256 is recorded beside
it in `dist/SHA256SUMS`. See [release notes](docs/RELEASE-v0.22.0a2.md) for validation
and reproduction instructions. No raw conversations, session databases or local
test journals are included. This release is not on PyPI.

## Original filtered snapshot: 0.21.0a1

The original snapshot was a deliberately filtered copy, not the original local evidence archive. Source code, tests, approved authored synthetic fixtures and the original wheel were byte-for-byte copies. Runnable evaluation code is included; raw runs, transcripts, journals, generated destinations, session databases, logs, caches and environment directories are excluded.

Markdown references to omitted local artifacts are relabelled, and changed documents carry a banner. Historical numerical claims remain author-reported summaries; the omitted raw records cannot be independently inspected in this distribution. Synthetic fixtures are already exposed, not a fresh holdout. No new test, model, hosted-provider, or customer-pilot success is claimed by packaging.

`PUBLIC_MANIFEST.json` records original and public SHA256 values at the [initial snapshot](https://github.com/mahekara28/context-relay/tree/8d93ec2d2148c16a79c4777e215cd810f7d7576a). It is retained as historical provenance and is not a manifest of the current source. Those hashes identify content, not independent authentication. Original files and their hashes remain unchanged locally.

Before publishing, review the staged files using [the publishing guide](docs/PUBLISHING.md). Pattern scanning cannot detect every secret. Never use the development workspace itself as a release copy.
