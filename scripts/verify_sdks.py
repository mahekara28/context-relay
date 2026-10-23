"""Run optional real-SDK tests locally and retain reproducible release evidence.

Run in an isolated environment with the optional SDKs installed. This script
never installs packages or invokes a model. SDK HTTP responses are simulated by
the tests; full-suite mode also includes the normal dependency-free tests.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import platform
import sys
import time
import unittest


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    from context_relay import __version__
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-suite", action="store_true")
    parser.add_argument("--report", type=Path, default=Path(f"evaluations/sdk-verification-v{__version__.rsplit('.', 1)[0]}.json"))
    args = parser.parse_args(argv)
    report = args.report
    paths = (report, report.with_suffix(".log"), report.with_suffix(".requirements.txt"))
    if any(path.exists() for path in paths):
        parser.error("Choose a new report path; prior reports, logs and dependency records are never overwritten.")
    packages = ("openai", "anthropic", "ollama", "langchain-core", "langgraph", "httpx", "httpx2", "pydantic")
    versions = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            raise SystemExit(f"Optional test dependency {package!r} is missing; install the SDK test environment first.")
    pattern = "test*.py" if args.full_suite else "test_real_sdks.py"
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=pattern)
    stream = io.StringIO()
    start = time.monotonic()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    elapsed = time.monotonic() - start
    raw = stream.getvalue()
    print(raw, end="")
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.with_suffix(".log").open("x", encoding="utf-8") as log:
        log.write(raw)
    files = sorted((root / "src" / "context_relay").rglob("*.py"))
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.relative_to(root).as_posix().encode())
        digest.update(file.read_bytes())
    evidence = {
        "schema": 1, "suite_pattern": pattern,
        "relay_version": __version__, "library_under_test": "this source tree",
        "verification": "actual installed SDKs and frameworks; simulated HTTPX/HTTPX2 MockTransport responses",
        "hosted_model_calls": 0, "hosted_checks": "NOT_RUN", "cryptographic_signature_validity_tested": False,
        "provider_model_acceptance_tested": False,
        "python": platform.python_version(), "platform": platform.system(),
        "versions": versions, "source_sha256": digest.hexdigest(),
        "sdk_tests_sha256": hashlib.sha256((root / "tests" / "test_real_sdks.py").read_bytes()).hexdigest(),
        "tests_run": result.testsRun,
        "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
        "failures": [{"test": str(test), "traceback": text} for test, text in result.failures],
        "errors": [{"test": str(test), "traceback": text} for test, text in result.errors],
        "skipped": [{"test": str(test), "reason": reason} for test, reason in result.skipped],
        "seconds": round(elapsed, 3), "success": result.wasSuccessful() and not result.skipped,
        "limitations": [
            "Simulated responses cannot verify opaque signatures or provider-side compaction correctness.",
            "No hosted model inference, quality measurement, or production deployment is covered.",
            "Native adapters preserve supported payloads; unsupported protocol families fail closed.",
        ],
    }
    with report.open("x", encoding="utf-8") as output:
        output.write(json.dumps(evidence, indent=2) + "\n")
    locked = sorted(f"{dist.metadata['Name']}=={dist.version}" for dist in importlib.metadata.distributions())
    with report.with_suffix(".requirements.txt").open("x", encoding="utf-8") as requirements:
        requirements.write("\n".join(locked) + "\n")
    print(f"Evidence: {report}")
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    sys.exit(main())
