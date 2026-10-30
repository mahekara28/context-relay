"""Run the disclosed fixture oracle and confirm its initial stale failure."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


FIXTURE = Path(__file__).resolve().parent / "fixtures"


def main():
    with tempfile.TemporaryDirectory(prefix="relay-continuation-") as folder:
        target = Path(folder)
        for name in ("reporting.py", "test_expected.py"):
            shutil.copy2(FIXTURE / name, target / name)
        result = subprocess.run([sys.executable, "-m", "unittest", "-q", "test_expected"],
                                cwd=target, capture_output=True, text=True, check=False)
        if result.returncode == 0 or "test_current_json_net_revenue" not in result.stderr:
            print("Baseline did not exhibit the expected stale-format failure.", file=sys.stderr)
            return 1
        if "Ran 2 tests" not in result.stderr:
            print("The independent oracle did not run both cases.", file=sys.stderr)
            return 1
        print("Baseline: expected failure reproduced; current JSON task remains unfinished.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
