"""Small real destination for a free, local Responses workflow pilot.

Approved release data and exported documents live in SQLite. The destination
atomically checks the source version and request ID; files are reproducible views
of committed documents. A failed file write is reconciled, never blindly retried.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import time
import uuid

from context_relay import EvidenceSource, OutcomePolicy, Principal, RelayConfig, SessionManager


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


SCHEMA = {"type": "object", "properties": {
    "request_id": {"type": "string"}, "source_version": {"type": "string"},
    "format": {"type": "string", "enum": ["JSON", "CSV"]}, "release": {"type": "string"},
    "artifacts": {"type": "array", "items": {"type": "object", "properties": {
        "filename": {"type": "string"}, "bytes": {"type": "integer"}, "sha256": {"type": "string"}},
        "required": ["filename", "bytes", "sha256"], "additionalProperties": False}},
}, "required": ["request_id", "source_version", "format", "release", "artifacts"], "additionalProperties": False}

INSTRUCTIONS = (
    "Export the approved release manifest by calling export_manifest exactly once. "
    "Copy the current request ID, source version, release and artifacts from Relay workflow facts. "
    "Use the current effective export_format preference (including a one-time override). "
    "Preserve filenames, byte counts and SHA256 values exactly. Do not invent or summarize artifact data."
)


def contracts():
    return {"export_manifest": {"when": {"may_export": True},
        "bindings": {"request_id": "workflow_request_id", "source_version": "workflow_source_version",
                     "release": "approved_release", "artifacts": "approved_artifacts"},
        "memory_bindings": {"format": "export_format"},
        "outcome": OutcomePolicy(required={"/ok": True})}}


class ReleaseDesk:
    """Example host service, not part of Relay's protection boundary."""
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / "destination.sqlite3"
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS source (id INTEGER PRIMARY KEY CHECK(id=1), version TEXT NOT NULL, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS exports (request_id TEXT PRIMARY KEY, arguments_hash TEXT NOT NULL, document TEXT NOT NULL, receipt TEXT NOT NULL)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def set_source(self, release, artifacts, *, may_export=True):
        if type(release) is not str or not release or type(may_export) is not bool:
            raise ValueError("A release name and boolean permission are required.")
        if type(artifacts) is not list or not 1 <= len(artifacts) <= 20:
            raise ValueError("Supply 1 through 20 approved artifacts.")
        for row in artifacts:
            if (type(row) is not dict or set(row) != {"filename", "bytes", "sha256"}
                    or type(row["filename"]) is not str or not row["filename"]
                    or type(row["bytes"]) is not int or row["bytes"] < 0
                    or type(row["sha256"]) is not str or len(row["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in row["sha256"])):
                raise ValueError("Each artifact needs a filename, nonnegative byte count and lowercase SHA256.")
        payload = encoded({"approved_release": release, "approved_artifacts": artifacts, "may_export": may_export})
        with self._db() as db:
            db.execute("INSERT INTO source VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,payload=excluded.payload",
                       (digest(payload), payload))
        return digest(payload)

    def read_evidence(self):
        with self._db() as db:
            row = db.execute("SELECT version,payload FROM source WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Load the approved release data first.")
        return {"version": row[0], "facts": json.loads(row[1]), "observed_at": time.time()}

    def export_manifest(self, request_id, source_version, format, release, artifacts):
        arguments = dict(request_id=request_id, source_version=source_version, format=format,
                         release=release, artifacts=artifacts)
        fingerprint = digest(encoded(arguments))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT arguments_hash,document,receipt FROM exports WHERE request_id=?", (request_id,)).fetchone()
            if existing is not None:
                if existing[0] != fingerprint:
                    raise ValueError("This destination request ID has a different payload.")
                document, receipt = existing[1], json.loads(existing[2])
            else:
                current = db.execute("SELECT version,payload FROM source WHERE id=1").fetchone()
                source = None if current is None else json.loads(current[1])
                if (source is None or current[0] != source_version or source["may_export"] is not True
                        or encoded(source["approved_artifacts"]) != encoded(artifacts)
                        or source["approved_release"] != release or format not in {"JSON", "CSV"}):
                    return {"ok": False, "reason": "destination_precondition_failed"}
                if format == "JSON":
                    document = json.dumps({"release": release, "artifacts": artifacts}, ensure_ascii=False, indent=2) + "\n"
                else:
                    stream = io.StringIO(newline="")
                    writer = csv.writer(stream, lineterminator="\n")
                    writer.writerow(["release", "filename", "bytes", "sha256"])
                    for item in artifacts:
                        writer.writerow([release, item["filename"], item["bytes"], item["sha256"]])
                    document = stream.getvalue()
                receipt = {"ok": True, "request_id": request_id, "format": format, "release": release,
                           "records": len(artifacts), "document_sha256": digest(document)}
                db.execute("INSERT INTO exports VALUES(?,?,?,?)", (request_id, fingerprint, document, encoded(receipt)))
        self._materialize(request_id, document, receipt)
        return receipt

    def _materialize(self, request_id, document, receipt):
        target = self.directory / (digest(request_id) + "." + receipt["format"].lower())
        if target.exists():
            if target.read_bytes() != document.encode("utf-8"):
                raise ValueError("Existing export file does not match the committed document.")
            return target
        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(document, encoding="utf-8", newline="")
        temporary.replace(target)
        return target

    def reconcile(self, request_id):
        with self._db() as db:
            row = db.execute("SELECT document,receipt FROM exports WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            return {"ok": False, "reason": "destination_has_no_committed_export"}
        receipt = json.loads(row[1])
        self._materialize(request_id, row[0], receipt)
        return receipt

    def documents(self):
        with self._db() as db:
            return [{"request_id": row[0], "document": row[1], "receipt": json.loads(row[2])}
                    for row in db.execute("SELECT request_id,document,receipt FROM exports ORDER BY request_id")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="JSON with release and artifacts fields")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--message", default="Export as JSON.")
    args = parser.parse_args()
    from openai import OpenAI  # Optional SDK; inference stays on the local Ollama server.
    desk = ReleaseDesk(args.directory / "destination")
    approved = json.loads(args.source.read_text(encoding="utf-8"))
    desk.set_source(approved["release"], approved["artifacts"])
    identity = Principal("local", "release-maintainer")
    with OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="ollama", max_retries=0, timeout=180) as client:
        with SessionManager(args.directory / "sessions", authorize=lambda principal, action: principal == identity) as manager:
            with manager.connect(client, principal=identity, conversation="release-desk", api="responses", native=True,
                                 config=RelayConfig(max_chars=64000)) as session:
                workflow = session.responses_workflow({"export_manifest": desk.export_manifest},
                    contracts=contracts(), schemas={"export_manifest": SCHEMA}, instructions=INSTRUCTIONS,
                    evidence=EvidenceSource(desk.read_evidence))
                result = workflow.send(args.message, request_id=args.request_id, model=args.model, temperature=0,
                                       max_output_tokens=2048)
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
