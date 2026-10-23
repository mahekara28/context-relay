# Preparing a public alpha

Publish a reviewed public copy, not the development workspace. Local evaluation
reports can contain complete requests, responses, session identifiers, paths and
generated business data. Keep those original records locally; a `.gitignore`
does not remove files already tracked by Git.

The public distribution builder uses a file allowlist. It keeps the Python
library, UI, tests, authored synthetic fixtures, examples, runnable evaluation
scripts, documentation, licence and a verified final wheel. It excludes raw run
reports, transcripts, journals, generated destinations, session databases,
environment directories and caches. Core installation has no runtime dependencies.

In the maintainer workspace, preflight is read-only:

```powershell
python work/prepare_public_release_v021.py --self-test
python work/prepare_public_release_v021.py
```

The public copy also contains the same builder at
`scripts/prepare_public_release.py`; use that path when working from a public
checkout. `--source PATH` selects a different source tree. Preflight checks the
selected text for recognizable tokens, personal home paths and session-like
UUIDs without printing matched values. It checks Python literal references to
existing evaluation JSON for missing fixture review. These checks are aids to
review, not proof that all sensitive content has been found.

After final checks and the final wheel are ready, create a fresh distribution:

```powershell
python work/prepare_public_release_v021.py --build --wheel PATH_TO_FINAL_WHEEL/context_relay_local-0.21.0a1-py3-none-any.whl
```

The default destination is `outputs/relay-v0.21.0a1-public` beside the development
project, with a ZIP alongside it. The builder refuses to overwrite either.
It checks that the wheel's version and all packaged Python/UI bytes match the
source. It does not build, install, publish, upload, call a model or access the
network. A failed build can leave a partial fresh directory for inspection; it
never deletes existing files. To retry, choose a fresh `--output PATH`.

`PUBLIC_DISTRIBUTION.md` explains the exclusions. `PUBLIC_MANIFEST.json` records
original and public hashes. Public Markdown copies label links to omitted local
reports rather than leaving broken evidence links; affected documents carry an
explicit derivative notice. The original historical documents and raw evidence
are unchanged. Historical results in these public summaries are author-reported;
their omitted raw records cannot be independently audited from this distribution.
Published synthetic fixtures are exposed regression material, not fresh holdouts.

Before pushing, inspect the public directory and Git's staged file list. Check
that no credentials, private source material, user conversations, `.relay`
storage, databases or raw evaluation runs are staged. The included `.gitignore`
keeps future local runs out by default. Files already tracked remain tracked
until explicitly removed from the index. Bundled wheels under `dist/` are
ignored by Git; they can be attached to a release separately after review.

Use the public directory as the repository root. The alpha CI configuration is
included, but configured operating-system/Python jobs are not evidence they
have passed until those jobs actually run. No repository, upload or release is
created automatically by this tooling.
