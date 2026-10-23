# Local storage and privacy

Relay stores facts, instructions, conversation history, tool arguments and
results. A persistent database can therefore contain private application data.
Relay does **not** encrypt that database. Local execution does not make the
data encrypted, and a hashed conversation filename is not anonymization.

## What changes in 0.21

- A new database is created with private permissions before SQLite opens it.
  On Windows, Relay uses a protected DACL granting the current process user and
  SYSTEM full control. On POSIX, it requests owner-only file mode `0600`.
- New named-storage directories use a protected, inheritable owner/SYSTEM DACL
  on Windows and mode `0700` on POSIX. Intermediate directories created by Relay
  receive the same restriction. Existing directories and files are unchanged.
- Every Relay connection enables SQLite `secure_delete=ON` and keeps SQLite
  temporary tables in memory. These reduce ordinary deleted-page remnants and
  temporary disk writes; neither is encryption or certified erasure.
- A content-free status report, explicit WAL checkpoint, and optional retention
  review helper expose the actual boundary.

New file creation fails if the OS refuses the requested creation operation;
Relay does not retry by creating an unrestricted file. Windows creation uses
standard security descriptors through the OS APIs, with no additional package
or shell command. It does not grant privacy against administrators, SYSTEM,
backup privileges, another process running as the same user, or a compromised
host application. Filesystems can have additional access and encryption rules.

Use an application-owned private directory. In particular, a direct
`RelayStore("existing-shared-directory/store.sqlite3")` restricts its new main
file but does not change that existing directory. SQLite sidecars on Windows
inherit directory permissions. Do not assume they inherit the main database's
private DACL. Named storage in a newly created private directory avoids this
ordinary inheritance problem. Existing shared directories need host review.

The POSIX mode request is subject to the process umask, which can make it more
restrictive. Relay never changes the global umask. Existing symlink/device
paths for the database and SQLite journals are rejected. These checks are not
a filesystem sandbox against a local process replacing files concurrently.

## Inspect protection

```python
status = agent.guard.store.privacy_status()
print(status["persistent_plaintext"])   # True for a file database
print(status["encrypted_by_relay"])     # False
print(status["creation_protection"])
```

The report contains no database path, session ID, customer content or principal.
It distinguishes protections applied at creation from current observations.
`permissions.current_access_verified` is deliberately false: Relay does not
attest current Windows ACLs, parent-directory access, POSIX extended ACLs,
privileged access, or disk encryption. POSIX file modes and ownership are
reported where available. A `secure_delete` value is read from the active
connection instead of assumed. Other applications opening SQLite can use
different settings.

An in-memory connection reports `persistent_plaintext=False`, but process
memory is plaintext. OS swap, crash dumps, application logs and provider-side
retention are outside this report. Use host-managed filesystem/volume encryption
when encryption at rest is required. Relay ships no encryption-key manager or
SQLCipher integration in this release.

## Delete one reviewed history

The existing conversation/workflow deletion APIs remove the whole selected
history and its memory, policies, checks and replay records. They reject pending
or in-flight requests and require an unchanged preview. The helper below uses
those same public methods, including a SessionManager facade's current `manage`
authorization; it never unwraps the owner to reach a raw store.

```python
from context_relay.storage_privacy import HistoryRetention

retention = HistoryRetention(chat)  # Or a ResponsesWorkflow/scoped history.
plan = retention.plan()
# Review and retain this host-owned plan; deletion is explicit.
result = retention.delete(expected=plan)
```

There is no required delay. A host can choose `grace_seconds=86400`, retain the
plan, and invoke deletion after that interval. New history, a revision change
or a changed grace policy invalidates the old plan. Pending requests still
block deletion even when the grace period has elapsed. The grace period starts
when the plan is reviewed; it is not an inferred last-activity timestamp.
Plans are ordinary host-owned records, not signed permissions or authenticated
instructions from an LLM. No scheduler or background purge runs automatically.

**Deleting an entire history also deletes its completed request-ID ledger.**
Retire those session/request IDs and maintain destination idempotency outside
Relay. The helper does not prune completed IDs from a live history to make room,
retry a failed request, reconcile an uncertain effect, or run a tool. Retention
does not make unresolved external actions safe to forget. Deleting a named
history means reopening its name starts fresh.

## Checkpoint deliberately

```python
result = agent.guard.store.checkpoint(mode="passive")
# During an explicit maintenance window, attempt WAL truncation:
result = agent.guard.store.checkpoint(mode="truncate")
```

Only `passive` and `truncate` are accepted. The result reports `busy`, frame
counts, `complete`, `wal_available` and `wal_truncated`. A concurrent reader can
prevent truncation; a busy result is never reported as successful truncation.
The configured SQLite busy timeout still applies. Checkpointing inside a Relay
transaction or executor callback is rejected. Checkpointing does not delete
logical records, discard pending action receipts, or retry any effect.

Successful WAL truncation is **not** physical-erasure certification.
`secure_delete` does not comprehensively scrub virtual-table shadow content,
previous WAL versions, filesystem snapshots, backups, SSD remapping, host logs,
exported reports or data already sent to a model provider. Existing text-history
deletion rebuilds its FTS index, but this still cannot erase external copies.

## Before publishing evidence or a support bundle

SQLite exclusions alone are insufficient. Pilot reports, model-call journals,
exception traces and frozen plans can contain absolute paths/usernames, original
prompts, native responses, provider/session IDs, tool arguments and receipts.
In the retained local 0.20 pilot evidence, absolute workstation paths and raw
synthetic requests/responses are present. These are local audit artifacts, not
automatically privacy-reviewed public data.

Keep original evidence private and unchanged. Publish an explicit allowlist of
reviewed source, synthetic fixtures and summaries, or separately labeled
sanitized derivatives. A redacted copy has different bytes and hashes; do not
present it as the original audited artifact. Credential redaction alone does
not remove private business content. Relay's storage controls do not sanitize
application logs or release archives.

## Validation scope

`tests/test_storage_privacy_v021.py` checks real Windows ACLs for the new private
directory, main database and live WAL/SHM files; plaintext disclosure; busy
checkpoint behavior; retained pending/replay records; stale deletion plans;
optional grace; and current scoped authorization. POSIX mode checks run on
POSIX and are skipped on Windows. These are local regression tests, not an
independent security audit or physical-erasure test.
