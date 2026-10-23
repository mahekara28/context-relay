"""Run the installed basic-memory check with a local stand-in; no AI calls."""
from context_relay.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["quickstart"]))
