"""Run directly from a checkout, without pip or internet access."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from context_relay.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
