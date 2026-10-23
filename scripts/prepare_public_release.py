"""Prepare an explicitly derived public copy; default is a read-only preflight.

No network, installation, model calls, deletions, or changes to source evidence.
"""
from __future__ import annotations

import argparse
import ast
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from urllib.parse import unquote, urlsplit
import zipfile


FIXTURES = frozenset({
    "corrections-v0.11-corpus.json", "corrections-v0.12-development-corpus.json",
    "corrections-v0.14-holdout.json", "corrections-v0.15-fresh.json",
    "instructions-v0.18-independent.json", "framework-v020-fixtures.json",
    "content-integrity-v0.16-cases.json", "interpretation-v0.12-corpus.json",
})
ROOT_FILES = (
    ".gitignore", "README.md", "LICENSE", "MANIFEST.in", "pyproject.toml",
    "run.py", "start.cmd", "PRODUCT.md", "requirements-validation.txt",
    "requirements-validation-constraints.txt",
)
TREE_TYPES = {
    "src/context_relay": {".py", ".html", ".css", ".js", ".typed"},
    "examples": {".py"}, "scripts": {".py"}, "tests": {".py"},
    "docs": {".md"}, ".github/workflows": {".yml", ".yaml"},
}
PATTERNS = {
    "personal_home_path": re.compile(
        r"(?i)(?:[a-z]:[\\/]+Users[\\/]+[^\\/\s<>\"']+|/(?:Users|home)/[^/\s<>\"']+)"),
    "secret_token": re.compile(
        r"(?:\bsk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{24,}|\bgh[pousr]_[A-Za-z0-9]{30,}"
        r"|\bAKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"),
    "session_like_uuid": re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I),
}
LINK = re.compile(r"(!?)\[([^\]\n]*)\]\((<[^>\n]+>|[^)\s]+)(?:\s+\"[^\"\n]*\")?\)")
DERIVED_NOTE = (
    "> Public derivative: omitted local evidence links are labelled below. "
    "The original document and raw evidence remain unchanged in the private working copy. "
    "This copy does not independently reproduce historical results.\n\n"
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_version(root: Path) -> str:
    match = re.search(r'^version = "([0-9A-Za-z.]+)"$', (root / "pyproject.toml").read_text(), re.M)
    if match is None:
        raise ValueError("Cannot identify source version")
    return match[1]


def selected_files(root: Path) -> dict[str, Path]:
    selected = {name: root / name for name in ROOT_FILES}
    for folder, extensions in TREE_TYPES.items():
        for file in (root / folder).rglob("*"):
            relative = file.relative_to(root)
            if (file.is_file() and file.suffix in extensions
                    and not any(part.startswith(".") or part == "__pycache__"
                                or part.endswith(".egg-info") for part in relative.parts[1:])):
                selected[relative.as_posix()] = file
    for pattern in ("*.py", "*.md"):
        for file in (root / "evaluations").glob(pattern):
            selected[file.relative_to(root).as_posix()] = file
    for name in FIXTURES:
        selected["evaluations/" + name] = root / "evaluations" / name
    for file in (root / "tests/fixtures").rglob("*.json"):
        selected[file.relative_to(root).as_posix()] = file
    for name, file in selected.items():
        if not file.is_file() or file.is_symlink() or root not in file.resolve().parents:
            raise ValueError(f"Missing or unsafe allowlisted file: {name}")
        if any(parent.is_symlink() for parent in file.parents if parent != root and root in parent.parents):
            raise ValueError(f"Symlinked source directory: {name}")
    return selected


def scan(name: str, data: bytes) -> list[dict]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [{"file": name, "category": "unexpected_non_utf8"}]
    return [{"file": name, "category": category, "matches": len(matches)}
            for category, pattern in PATTERNS.items()
            if (matches := list(pattern.finditer(text)))]


def public_markdown(name: str, data: bytes, retained: set[str]) -> tuple[bytes, int]:
    count = 0

    def replace(match):
        nonlocal count
        target = match[3].strip("<>")
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or not parsed.path:
            return match[0]
        joined = PurePosixPath(name).parent / unquote(parsed.path).replace("\\", "/")
        parts = []
        for part in joined.parts:
            if part == "..":
                if parts:
                    parts.pop()
                else:
                    parts.append("..")
            elif part != ".":
                parts.append(part)
        destination = "/".join(parts)
        if destination in retained or any(p.startswith(destination.rstrip("/") + "/") for p in retained):
            return match[0]
        count += 1
        return match[2] + " (local-only reference; omitted from public distribution)"

    text = LINK.sub(replace, data.decode("utf-8-sig"))
    if count:
        text = DERIVED_NOTE + text
    return text.encode("utf-8") if count else data, count


def fixture_dependencies(root: Path, selected: dict[str, Path]) -> list[dict]:
    """Find existing evaluation JSON named in Python literals; never treat run outputs as fixtures."""
    missing = []
    candidates = {p.name for p in (root / "evaluations").glob("*.json")}
    for name, file in selected.items():
        if file.suffix != ".py":
            continue
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value.replace("\\", "/")
                base = value.rsplit("/", 1)[-1]
                if base in candidates and base not in FIXTURES:
                    missing.append({"file": name, "line": node.lineno, "fixture": base})
    return missing


def wheel_payload(wheel: Path, version: str, source: dict[str, bytes]) -> bytes:
    if wheel.name != f"context_relay_local-{version}-py3-none-any.whl" or wheel.is_symlink():
        raise ValueError("Expected the final, regular-file wheel for this source version")
    raw = wheel.read_bytes()
    with zipfile.ZipFile(wheel) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("Invalid wheel archive")
        expected = {name.removeprefix("src/"): data for name, data in source.items() if name.startswith("src/")}
        actual = {name for name in archive.namelist() if name.startswith("context_relay/")}
        if actual != set(expected) or any(archive.read(name) != data for name, data in expected.items()):
            raise ValueError("Wheel package files do not exactly match the selected current source/UI")
        metadata = f"context_relay_local-{version}.dist-info/METADATA"
        package = BytesParser().parsebytes(archive.read(metadata))
        if package["Name"] != "context-relay-local" or package["Version"] != version or package.get_all("Requires-Dist"):
            raise ValueError("Wheel metadata version/name/dependency check failed")
        for name in archive.namelist():
            if not (name.startswith("context_relay/") or name.startswith(f"context_relay_local-{version}.dist-info/")):
                raise ValueError("Unexpected wheel member")
            if ".." in PurePosixPath(name).parts or name.startswith("/"):
                raise ValueError("Unsafe wheel member")
            findings = scan("dist/" + wheel.name + "!" + name, archive.read(name))
            if findings:
                raise ValueError(json.dumps({"wheel_scan_findings": findings}))
    return raw


def prepare(root: Path, wheel: Path | None = None) -> tuple[str, dict[str, bytes], dict]:
    selected = selected_files(root)
    version = source_version(root)
    original = {name: file.read_bytes() for name, file in selected.items()}
    original["scripts/prepare_public_release.py"] = Path(__file__).read_bytes()
    dependencies = fixture_dependencies(root, selected)
    findings = [item for name, data in original.items() for item in scan(name, data)]
    if dependencies or findings:
        raise ValueError(json.dumps({"missing_fixture_review": dependencies, "scan_findings": findings}))
    public, changes = {}, {}
    retained = set(original) | {"PUBLIC_DISTRIBUTION.md", "PUBLIC_MANIFEST.json"}
    if wheel is not None:
        retained.add("dist/" + wheel.name)
    for name, data in original.items():
        public[name], count = public_markdown(name, data, retained) if name.endswith(".md") else (data, 0)
        if count:
            changes[name] = count
    if wheel is not None:
        public["dist/" + wheel.name] = wheel_payload(wheel, version, original)
        original["dist/" + wheel.name] = public["dist/" + wheel.name]
    omitted_count = sum(1 for file in (root / "evaluations").rglob("*")
                        if file.is_file() and file.relative_to(root).as_posix() not in selected)
    manifest = {
        "schema": 1, "kind": "derived_public_distribution", "version": version,
        "raw_local_evidence_included": False, "source_files_modified": False,
        "omitted_evaluation_files": omitted_count, "markdown_links_relabelled": sum(changes.values()),
        "files": [{"path": name, "source_sha256": sha(original[name]), "public_sha256": sha(data),
                   "omitted_local_links": changes.get(name, 0)} for name, data in sorted(public.items())],
        "scan": {"personal_home_paths": 0, "recognizable_secret_tokens": 0, "session_like_uuids": 0,
                 "limitation": "Pattern checks are not a complete secret or privacy audit."},
    }
    public["PUBLIC_MANIFEST.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    public["PUBLIC_DISTRIBUTION.md"] = (
        f"# Derived public distribution — Relay {version}\n\n"
        "This is a deliberately filtered copy, not the original local evidence archive. "
        "Source code, tests, approved authored synthetic fixtures and the supplied wheel are byte-for-byte copies. "
        "Runnable evaluation code is included; raw runs, transcripts, journals, generated destinations, "
        "session databases, logs, caches and environment directories are excluded.\n\n"
        "Markdown references to omitted local artifacts are relabelled, and changed documents carry a banner. "
        "Historical numerical claims remain author-reported summaries; the omitted raw records cannot be "
        "independently inspected in this distribution. Synthetic fixtures are already exposed, not a fresh holdout. "
        "No new test, model, hosted-provider, or customer-pilot success is claimed by packaging.\n\n"
        "`PUBLIC_MANIFEST.json` records original and public SHA256 values for included files. "
        "Those hashes identify content, not independent authentication. The manifest and this notice are generated "
        "derivatives and do not hash themselves. Original files and their hashes remain unchanged locally.\n\n"
        "Before publishing, review the staged files using [the publishing guide](docs/PUBLISHING.md). "
        "Pattern scanning cannot detect every secret. Never use the development workspace itself as a release copy.\n"
    ).encode()
    # Re-read to catch concurrent source edits before reporting a stable preflight/build.
    if any(file.read_bytes() != original[name] for name, file in selected.items()):
        raise ValueError("Source changed during preflight; retry after source freeze")
    summary = {"mode": "preflight", "version": version, "files": len(public),
               "wheel_checked": wheel is not None, "fixture_files": len(FIXTURES) + sum(
                   name.startswith("tests/fixtures/") for name in selected),
               "omitted_evaluation_files": omitted_count, "derived_markdown_files": len(changes),
               "relabelled_links": sum(changes.values()), "scan_findings": 0,
               "source_files_unchanged": True, "missing_fixture_dependencies": dependencies}
    return version, public, summary


def build(root: Path, target: Path, wheel: Path) -> dict:
    version, files, summary = prepare(root, wheel)
    if target == root or root in target.parents or target in root.parents:
        raise ValueError("Public output must be separate from the source directory")
    archive = target.with_name(target.name + ".zip")
    if target.exists() or archive.exists():
        raise FileExistsError("Public directory or ZIP already exists; choose a fresh output path")
    target.mkdir(parents=True, exist_ok=False)
    for name, data in sorted(files.items()):
        file = target / name
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("xb") as stream:
            stream.write(data)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as output:
        for name, data in sorted(files.items()):
            output.writestr(target.name + "/" + name, data)
    with zipfile.ZipFile(archive) as check:
        if check.testzip() is not None:
            raise ValueError("Public ZIP integrity failed")
    return {**summary, "mode": "built", "directory": target.name, "archive": archive.name,
            "archive_sha256": sha(archive.read_bytes()), "version": version}


def self_test() -> dict:
    checks = 0
    data = b"[raw](old.json) [source](../src/a.py) [web](https://example.com) [anchor](#ok)"
    rewritten, count = public_markdown("docs/a.md", data, {"docs/a.md", "src/a.py"})
    assert count == 1 and b"(old.json)" not in rewritten and b"[source]" in rewritten
    assert rewritten.startswith(DERIVED_NOTE.encode())
    checks += 1
    assert not scan("safe.py", b"api_key = os.environ['API_KEY']")
    for category, sample in (("personal_home_path", "C:/Users/" + "example/private"),
                             ("secret_token", "sk-" + "x" * 30),
                             ("session_like_uuid", "12345678-1234-" + "4123-8123-123456789012")):
        assert any(row["category"] == category for row in scan("fixture.txt", sample.encode()))
        checks += 1
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for name in ROOT_FILES:
            (root / name).write_text('version = "0.21.0a1"\n' if name == "pyproject.toml" else "safe")
        (root / "evaluations").mkdir()
        for name in FIXTURES:
            (root / "evaluations" / name).write_text("{}")
        for name in ("raw.json", "private.log", "journal.jsonl", "state.sqlite3"):
            (root / "evaluations" / name).write_text("private")
        (root / "evaluations/nested-run").mkdir()
        (root / "evaluations/nested-run/runner.py").write_text("private")
        files = selected_files(root)
        assert all("raw.json" not in name and "private.log" not in name and "nested-run" not in name
                   and not name.endswith((".jsonl", ".sqlite3")) for name in files)
        checks += 1
        version = "0.21.0a1"
        wheel = root / f"context_relay_local-{version}-py3-none-any.whl"
        sample_source = {"src/context_relay/__init__.py": b"# authored fixture\n"}
        with zipfile.ZipFile(wheel, "x") as archive:
            archive.writestr("context_relay/__init__.py", sample_source["src/context_relay/__init__.py"])
            archive.writestr(f"context_relay_local-{version}.dist-info/METADATA",
                             f"Name: context-relay-local\nVersion: {version}\n\n")
        assert wheel_payload(wheel, version, sample_source) == wheel.read_bytes()
        checks += 1
        try:
            wheel_payload(wheel, version, {"src/context_relay/__init__.py": b"changed"})
        except ValueError:
            checks += 1
        else:
            raise AssertionError("Mismatched wheel was accepted")
    return {"self_tests_passed": checks, "release_artifacts_created": False}


def main() -> int:
    script_root = Path(__file__).resolve().parents[1]
    default_source = script_root if (script_root / "pyproject.toml").is_file() else script_root / "outputs/context-relay"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=default_source)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--build", action="store_true", help="Create a fresh public directory and ZIP; requires --wheel")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2))
        return 0
    root = args.source.resolve(strict=True)
    wheel = args.wheel.resolve(strict=True) if args.wheel else None
    if args.build:
        if wheel is None:
            parser.error("--build requires the final --wheel")
        version = source_version(root)
        target = (args.output or root.parent / f"relay-v{version}-public").resolve()
        result = build(root, target, wheel)
    else:
        result = prepare(root, wheel)[2]
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
