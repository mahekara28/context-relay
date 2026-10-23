from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import Relay, RelayStore, __version__
from .config import RelayConfig
from .memory import MemoryPolicy
from .demo import connection_demo, demo_context, seed_session
from ._storage_files import private_directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="context-relay", description="Local context checks for agent harnesses")
    parser.add_argument("--version", action="version", version=f"Context Relay {__version__}")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Open a loopback-only web workbench")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--db", default=".relay/workbench.db")
    sub.add_parser("demo", help="Run a deterministic context-loss demonstration")
    sub.add_parser("quickstart", help="Check basic memory offline; no AI client or account needed")
    init = sub.add_parser("init", help="Create a connection configuration file without overwriting one")
    init.add_argument("path", nargs="?", default="relay.json")
    init.add_argument("--memory", choices=("off", "auto", "review"), default="off",
                      help="Enable source-backed user recall without defining fields")
    init.add_argument("--protocol", choices=("plain", "ollama", "openai_chat"), default="plain")
    args = parser.parse_args(argv)
    if args.command == "quickstart":
        observed = connection_demo()
        passed = observed == ["CSV", "JSON"]
        print("Offline connection check: " + ("PASSED" if passed else "FAILED"))
        print("Preference received by the local stand-in: " + " -> ".join(observed))
        print("No AI, SDK or network calls. This checks memory wiring, not model quality.")
        print("Next: use auto_connect(existing_client) once per conversation and send messages through it.")
        return 0 if passed else 1
    if args.command == "init":
        try:
            path = Path(args.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("x", encoding="utf-8", newline="\n") as stream:
                    settings = RelayConfig(protocol=args.protocol,
                                           memory=None if args.memory == "off" else MemoryPolicy(mode=args.memory))
                    stream.write(json.dumps(settings.to_dict(), indent=2, ensure_ascii=False) + "\n")
            except FileExistsError:
                print(f"Configuration already exists: {args.path}. Choose another path; nothing was overwritten.",
                      file=sys.stderr)
                return 1
        except (OSError, ValueError) as exc:
            print(f"Could not create configuration: {exc}", file=sys.stderr)
            return 1
        print(f"Created {path.resolve()}")
        return 0
    if args.command == "demo":
        store = RelayStore()
        try:
            session = seed_session(store, "Refund demo", "refund")
            demo = demo_context(store, session["id"])
            relay = Relay(store)
            old = relay.check(session["id"], demo["proposed_action"])
            current = relay.check(session["id"], demo["safe_action"])
            print(json.dumps({"mode": "deterministic_demo", "old_action": old,
                              "current_action": current}, indent=2, ensure_ascii=False))
            return 0 if old["decision"] == "block" and current["decision"] == "allow" else 1
        finally:
            store.close()
    if args.command in {"serve", None}:
        from .server import make_server
        port = getattr(args, "port", 8765)
        if not 0 <= port <= 65535:
            parser.error("Port must be between 0 and 65535.")
        path = Path(getattr(args, "db", ".relay/workbench.db")).resolve()
        private_directory(path.parent)
        logging.basicConfig(level=logging.WARNING)
        store = RelayStore(path)
        try:
            server = make_server(store, port)
        except OSError as exc:
            store.close()
            parser.exit(1, f"Could not start the local workbench: {exc}\nTry --port 8766.\n")
        print(f"Context Relay: http://127.0.0.1:{server.server_port}", flush=True)
        print(f"Local data: {path}\nNo model connected. Demo actions are simulated. Press Ctrl+C to stop.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            store.close()
        return 0
    parser.print_help()
    return 0
