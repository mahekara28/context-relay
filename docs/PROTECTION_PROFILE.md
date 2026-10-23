# A protection profile for important actions

Use basic `auto_connect()` when you need supported preference memory. Use this
profile when an agent writes data and a stale preference or false completion
would matter. It combines existing application contracts with explicit review;
it does not automatically discover every tool or decide what is true.

The example below builds on [Release Desk](../examples/release_desk.py). That
host application stores approved release metadata, atomically checks source
versions and request IDs, and writes JSON/CSV manifests. Its tool contract binds
the request ID, source version and exact artifact records, checks `may_export`,
maps `format` to user memory and requires an `{"ok": true}` receipt.
For an already assembled local application, run
[Verified Release Desk](../examples/verified_release_desk.py) using the
[alpha commands](ALPHA.md).

## Check the source and the destination

Run from the source-bundle root after installing Relay. These functions compare
the host-selected artifact with the approved metadata and independently read
the committed export and its file. They are ordinary host code: keep their
configuration, identities and implementation under application control.

```python
import csv
import hashlib
import io
import json
from pathlib import Path

from context_relay import (
    CoveragePolicy, EvidenceSource, MemoryPolicy, OutcomeVerifier,
    Principal, RelayConfig, SessionManager, WorkflowHistoryPolicy,
)
from examples.release_desk import ReleaseDesk, SCHEMA, INSTRUCTIONS, contracts, digest, encoded

artifact = Path("dist/context_relay_local-0.21.0a1-py3-none-any.whl")
approved_release = "0.21.0a1"  # Selected by your trusted release process.
desk = ReleaseDesk(".relay/release-desk/destination")

def artifact_record():
    data = artifact.read_bytes()
    return {"filename": artifact.name, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}

# The host, not the model, approves this source and permission.
desk.set_source(approved_release, [artifact_record()], may_export=True)

def verify_release_files(snapshot):
    expected = {"approved_release": approved_release,
                "approved_artifacts": [artifact_record()], "may_export": True}
    return (encoded(snapshot.facts) == encoded(expected)
            and snapshot.version == digest(encoded(expected)))

def verify_destination(check):
    arguments = check["arguments"]
    if arguments is None:
        return None  # Intent is unavailable after some crashes; do not invent success.
    if (check["tool"] != "export_manifest"
            or arguments["request_id"] != check["request_id"]):
        return False
    rows = [row for row in desk.documents()
            if row["request_id"] == check["request_id"]]
    if len(rows) != 1 or rows[0]["receipt"] != check["result"]:
        return False
    row = rows[0]
    receipt = row["receipt"]
    expected_receipt = {
        "ok": True, "request_id": check["request_id"], "format": arguments["format"],
        "release": arguments["release"], "records": len(arguments["artifacts"]),
        "document_sha256": digest(row["document"]),
    }
    if encoded(receipt) != encoded(expected_receipt):
        return False
    path = desk.directory / (digest(check["request_id"]) + "." + receipt["format"].lower())
    stored = path.read_bytes()
    if (stored != row["document"].encode("utf-8")
            or hashlib.sha256(stored).hexdigest() != receipt["document_sha256"]):
        return False
    if arguments["format"] == "JSON":
        return encoded(json.loads(stored)) == encoded({
            "release": arguments["release"], "artifacts": arguments["artifacts"],
        })
    expected = [["release", "filename", "bytes", "sha256"]] + [
        [arguments["release"], item["filename"], str(item["bytes"]), item["sha256"]]
        for item in arguments["artifacts"]]
    return list(csv.reader(io.StringIO(stored.decode("utf-8")))) == expected
```

`EvidenceSource.verify` receives an `EvidenceSnapshot` and must return exactly
`True`. The reader still returns exactly `version`, `facts` and `observed_at`.
The workflow reads/verifies before inference and again before dispatch. This
sample validates artifact bytes; it does not establish who should approve a
release. An external source can change after the final read, so the destination
must enforce its own atomic version precondition and idempotency key.

`OutcomeVerifier` receives `request_id`, `tool`, checked `arguments` and `result`.
It must read the destination without repeating the original action. Arguments
can be `None` during reconciliation after a crash; this example then reports
unavailable because it cannot compare the output with known intent. A host can
instead recover that intent from an authoritative destination record. A false result rejects completion; a
failed or unavailable readback keeps the effect pending for reconciliation.
Neither callback is an independent truth service supplied by Relay.

## Connect the checked route

`existing_openai_client`, authenticated identity, `your_permission_check` and
`your_model` come from your application. A local compatible Responses endpoint
can use the same interface. The permission callback must return exactly `True`
for permitted operations; do not take identity or permission from model text.

```python
identity = Principal(authenticated_tenant, authenticated_user)
with SessionManager(".relay/release-desk/sessions",
                    authorize=your_permission_check) as manager:
    with manager.connect(
        existing_openai_client, principal=identity, conversation="release-export",
        api="responses", native=True, config=RelayConfig(max_chars=64_000),
    ) as session:
        workflow = session.responses_workflow(
            {"export_manifest": desk.export_manifest},
            contracts=contracts(), schemas={"export_manifest": SCHEMA},
            instructions=INSTRUCTIONS,
            memory=MemoryPolicy(assurance_fields=("export_format",)),
            coverage=CoveragePolicy(["export_manifest"]),
            evidence=EvidenceSource(
                desk.read_evidence, max_age_seconds=30,
                verify=verify_release_files, verifier_id="release-artifact-bytes-v1",
            ),
            outcome_verifier=OutcomeVerifier(
                verify_destination, verifier_id="export-document-and-file-v1",
            ),
            history_policy=WorkflowHistoryPolicy(rollover_after_chars=12_000),
            repair="known",
        )
        result = workflow.send("For this export only, use JSON.",
                               request_id=stable_request_id, model=your_model)
        if result.status == "review_required":
            plan = workflow.review_plan()
            # Present plan["memory"]["source"] and all selected fields for review.
            # Continue only after an explicit host/user choice, shown here as JSON:
            workflow.resolve_review(plan, decisions={
                "export_format": {"value": "JSON", "scope": "turn"},
            })
            result = workflow.resume_review(plan["request_id"])
        print(result.completed, result.business_status)
        report = workflow.protection_report()
```

The inline JSON choice illustrates a completed review. A real UI must obtain
that decision; do not blindly approve the previous suggestion. `resolve_review`
only records the review. `resume_review` continues the same stored request and
provider options without adding another user turn. Review can be completed
after restart with the same named session and identical workflow configuration.
Changed state invalidates an old plan.

Every new explicit user turn requires all `assurance_fields` to be reviewed,
even a harmless message or a clearly recognised instruction. `scope="turn"`
leaves the standing preference intact; `scope="durable"` updates it. Decisions
cannot grant permission, change trusted facts or silently clear an unrelated
durable dispute. Default memory remains available without this review gate.
In the fresh synthetic wording diagnostic, default extraction found only 1 of
11 clear changes. Requiring review covers missed instructions by stopping for a
decision; it is not an improvement in language recognition.

## What the profile establishes

`CoveragePolicy` requires the registered tools to match the declared inventory,
requires outcome checks by default and rejects undeclared unchecked arguments.
Intentional exceptions must match exactly, for example
`allow_unchecked={"export_manifest": ["note"]}` only if that tool actually has a
declared unchecked `note`. It cannot find hidden function references or protect
calls outside the registry. A checked parameter can still contain meaning the
declared rule does not examine.

`repair="known"` can restore supported arguments from reviewed preferences and
current evidence before the tool starts. It rechecks the proposal and cannot
change unrelated arguments. Set `repair=None` to reject without repair. Once an
effect starts, neither repair nor request replay repeats it. Inspect uncertain
effects at the destination and use explicit reconciliation. A rejected task is
unfinished, not a success.

Destination readback happens after the action. It can detect a bad result, but
does not undo an external effect or make a faulty tool implementation safe.

`WorkflowHistoryPolicy` opts into fresh native segments, not provider-native
compaction. The archive retains exact original segments, while the new input
omits old reasoning and detailed results. Retrieve details before relying on
them. The host explicitly calls `update_task()` or `finish_task()`; a successful
tool does not finish a larger task. Archive, user-history and request limits
still stop work when reached. See [continuation](WORKFLOW_CONTINUATION.md).

The report describes actual configured checks; it is not a security certificate.
Source and destination verifiers remain host-owned and can both be wrong. Local
SQLite is plaintext, and deletion cannot guarantee erasure from backups,
filesystem remnants or providers. This profile is synchronous, supports one
function call per response and has no established organisational or distributed
reliability claim. See [privacy](STORAGE_PRIVACY.md) and [validation](VALIDATION.md).
