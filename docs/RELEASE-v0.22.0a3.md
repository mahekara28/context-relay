# Relay 0.22.0a3

This patch keeps the [instruction and recovery fixes from 0.22.0a2](RELEASE-v0.22.0a2.md)
and fixes an intermittent error response in the local workbench server.

## What changed

An oversized upload was correctly rejected, but the server could close its socket
while the client was still sending. On Windows, the client sometimes received a
connection-aborted error instead of the intended HTTP 413 response. This surfaced
in the [Windows push check for 34bc759](https://github.com/mahekara28/context-relay/actions/runs/39996565628/job/120561501738).

Early JSON error responses now explicitly close the connection after sending the
response and briefly draining unread input. The drain uses at most 64 KiB per read,
with a 1 MiB total limit and a 0.25-second absolute deadline. A slow or endless
upload cannot extend that deadline. Already-consumed bodies skip the drain.
The approach follows the staged connection shutdown described in
[HTTP connection teardown guidance](https://www.rfc-editor.org/rfc/rfc9112.html#section-9.6).

The request size limit stays at 128 KiB. Rejected requests do not create sessions
or execute application work. This changes error delivery, not which inputs are
accepted. The workbench remains a loopback-only development server.

## Validation

Before the fix, an independent Windows probe received 98 HTTP 413 responses and
two connection-aborted errors from 100 oversized requests. With the fix, all 500
requests in the follow-up probe received the expected HTTP 413 JSON response;
none created a session. These are local observations, not a universal error-rate
estimate.

| Local Windows check | Result |
| --- | --- |
| Python 3.10.11, core environment | 1,844 tests: 1,779 passed, 65 optional SDK skips |
| Python 3.12.14, pinned SDK environment | 1,844 tests: 1,843 passed, one POSIX-only skip |
| Final server regression suite | 20 tests passed, including nine new tests |
| Clean installed wheel, outside the checkout | Offline quickstart and all 37 server, memory and recovery tests passed |

The final server tests use explicit coordination for delayed uploads instead of
assuming client and server scheduling. Separate tests check the byte limit, the
absolute deadline, response-before-drain ordering, incomplete uploads and the
absence of session mutations. The original oversized-request assertion still
requires an HTTP 413 response; connection-aborted errors are not accepted as passes.

Two local wheel builds are byte-identical with Python 3.12.14, setuptools 84.2.1
and `SOURCE_DATE_EPOCH=1792713600`. All 57 packaged library/assets files match the
tested source. The new SHA-256 is recorded in `dist/SHA256SUMS`.

These are local results. For hosted validation, inspect the matching commit in
[PR #2's checks](https://github.com/mahekara28/context-relay/pull/2/checks).
Optional SDK tests still use simulated transports.

## Upgrade

From the updated repository, use your application's Python environment:

```sh
python -m pip install --no-index --no-deps dist/context_relay_local-0.22.0a3-py3-none-any.whl
python -m context_relay quickstart
```

Restart running applications, including the workbench server. There is no schema
migration. The [0.22.0a2 session upgrade notes](RELEASE-v0.22.0a2.md#upgrade) still
apply. Existing wheels retain their original checksums.

The drain is deliberately bounded: clients that keep sending after rejection, or
exceed those bounds, can still encounter a closed connection. This patch does not
change Relay's finite instruction grammar, simulated provider-test coverage, or
application responsibilities for external effects and trusted destination readback.
