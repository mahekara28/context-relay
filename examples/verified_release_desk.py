"""Export real release artifacts with explicit review and physical-file readback.

This composes ReleaseDesk; Relay does not infer the review decision or provide
authentication. The CLI uses an existing local Ollama model without downloads.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from functools import wraps
import hashlib
import io
import json
from pathlib import Path

from context_relay import (CoveragePolicy, EvidenceSource, MemoryPolicy, OutcomeVerifier,
                           Principal, RelayConfig, SessionManager, WorkflowHistoryPolicy)
from release_desk import ReleaseDesk, SCHEMA, INSTRUCTIONS, contracts, digest, encoded


MODEL = "qwen2.5:1.5b"
MAX_OUTPUT_TOKENS = 384
HISTORY_POLICY = WorkflowHistoryPolicy(rollover_after_chars=5000, max_segments=128)


def read_artifacts(paths):
    """Read actual bytes, independently of the destination's source table."""
    paths = tuple(Path(path).resolve(strict=True) for path in paths)
    if not 1 <= len(paths) <= 20 or len({path.name for path in paths}) != len(paths):
        raise ValueError("Supply 1 through 20 files with distinct filenames.")
    result = []
    for path in paths:
        checksum, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(chunk)
                size += len(chunk)
        result.append({"filename": path.name, "bytes": size, "sha256": checksum.hexdigest()})
    return result


class VerifiedReleaseDesk:
    """Example host composition: artifact files, scoped workflow and readback."""
    def __init__(self, directory, *, release, artifacts, event=None):
        self.directory = Path(directory).resolve()
        self.release = release
        self.artifacts = tuple(Path(path).resolve(strict=True) for path in artifacts)
        self.event = event or (lambda kind, data: None)
        self.desk = ReleaseDesk(self.directory / "destination")
        self.source_version = self.desk.set_source(release, read_artifacts(self.artifacts))

    def verify_source(self, snapshot):
        expected = {"approved_release": self.release, "approved_artifacts": read_artifacts(self.artifacts),
                    "may_export": True}
        return encoded(snapshot.facts) == encoded(expected) and snapshot.version == digest(encoded(expected))

    def verify_output(self, request):
        """Read only. Missing intent/files is unavailable, not invented success."""
        arguments = request["arguments"]
        if arguments is None:
            return None
        if (request["tool"] != "export_manifest" or type(arguments) is not dict
                or set(arguments) != set(SCHEMA["properties"])
                or arguments["request_id"] != request["request_id"]
                or arguments["format"] not in ("CSV", "JSON")):
            return False
        records = [row for row in self.desk.documents() if row["request_id"] == request["request_id"]]
        if len(records) != 1:
            return False
        row = records[0]
        receipt, document = row["receipt"], row["document"]
        expected_receipt = {"ok": True, "request_id": request["request_id"], "format": arguments["format"],
            "release": arguments["release"], "records": len(arguments["artifacts"]), "document_sha256": digest(document)}
        if (encoded(request["result"]) != encoded(receipt) or receipt.get("ok") is not True
                or encoded(receipt) != encoded(expected_receipt)):
            return False
        path = self.desk.directory / (digest(request["request_id"]) + "." + arguments["format"].lower())
        try:
            contents = path.read_bytes()
        except FileNotFoundError:
            return None
        if contents != document.encode("utf-8"):
            return False
        if arguments["format"] == "JSON":
            return encoded(json.loads(contents)) == encoded({"release": arguments["release"], "artifacts": arguments["artifacts"]})
        actual = list(csv.reader(io.StringIO(contents.decode("utf-8"))))
        expected = [["release", "filename", "bytes", "sha256"]] + [
            [arguments["release"], item["filename"], str(item["bytes"]), item["sha256"]]
            for item in arguments["artifacts"]]
        return actual == expected

    @contextmanager
    def connect(self, client, *, conversation="verified-release-desk"):
        identity = Principal("local-example", "release-maintainer")
        @wraps(self.desk.export_manifest)
        def export(*args, **kwargs):
            arguments = dict(zip(SCHEMA["required"], args)) | kwargs
            self.event("tool_started", arguments)
            try:
                result = self.desk.export_manifest(*args, **kwargs)
            except BaseException as error:
                self.event("tool_failed", {"request_id": arguments.get("request_id"), "type": type(error).__name__})
                raise
            self.event("tool_returned", {"request_id": arguments.get("request_id"), "result": result})
            return result
        with SessionManager(self.directory / "sessions", authorize=lambda principal, action: principal == identity) as manager:
            with manager.connect(client, principal=identity, conversation=conversation, provider="openai",
                                 api="responses", native=True, config=RelayConfig(max_chars=16000)) as owner:
                yield owner.responses_workflow({"export_manifest": export}, contracts=contracts(),
                    schemas={"export_manifest": SCHEMA}, instructions=INSTRUCTIONS,
                    memory=MemoryPolicy(assurance_fields=("export_format",)), history_policy=HISTORY_POLICY,
                    coverage=CoveragePolicy(["export_manifest"]),
                    evidence=EvidenceSource(self.desk.read_evidence, max_age_seconds=300,
                        verify=self.verify_source, verifier_id="artifact-bytes-v1"),
                    outcome_verifier=OutcomeVerifier(self.verify_output, verifier_id="export-file-bytes-v1"))


def apply_review(workflow, plan, *, format, scope):
    """An explicit host decision, never a suggestion auto-approved by a model."""
    if format not in ("CSV", "JSON") or scope not in ("durable", "turn"):
        raise ValueError("Choose CSV/JSON and durable/turn explicitly.")
    return workflow.resolve_review(plan, decisions={"export_format": {"value": format, "scope": scope}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--artifact", type=Path, action="append", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--review-format", choices=("CSV", "JSON"))
    parser.add_argument("--review-scope", choices=("durable", "turn"))
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()
    if bool(args.review_format) != bool(args.review_scope):
        parser.error("Supply both --review-format and --review-scope, or neither.")
    from openai import OpenAI  # Optional SDK, installed separately by the host.
    application = VerifiedReleaseDesk(args.directory, release=args.release, artifacts=args.artifact)
    with OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="ollama", max_retries=0, timeout=120) as client:
        with application.connect(client) as workflow:
            options = {"model": args.model, "temperature": 0, "max_output_tokens": MAX_OUTPUT_TOKENS}
            pending = workflow.pending
            if pending is not None and pending["request_id"] == args.request_id and pending["phase"] == "review_pending":
                if pending["input"] != {"text": args.message, "options": options}:
                    raise ValueError("Pending request text/options must remain unchanged.")
                result = None
            else:
                result = workflow.send(args.message, request_id=args.request_id, **options)
            if result is None or result.status == "review_required":
                plan = workflow.review_plan()
                if args.review_format is None:
                    print(json.dumps({"status": "review_required", "plan": plan, "model_calls_for_review": 0}, indent=2))
                    return
                apply_review(workflow, plan, format=args.review_format, scope=args.review_scope)
                result = workflow.resume_review(args.request_id)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
