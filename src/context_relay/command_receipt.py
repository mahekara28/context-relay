"""Bounded command receipts without retaining command output or arguments."""

from __future__ import annotations

import hashlib
import subprocess
import threading
import time
from pathlib import Path

from .models import timestamp


def run_command(command: list[str], *, cwd: str | Path, timeout_seconds: int = 30) -> dict:
    """Execute an explicit argv and hash drained output without buffering it in memory."""
    if type(command) is not list or not command or len(command) > 32 or any(
        type(part) is not str or not part or len(part) > 4096 for part in command
    ):
        raise ValueError("command must be 1 to 32 nonempty argv strings.")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise ValueError("timeout_seconds must be from 1 through 300.")
    root = Path(cwd).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("cwd must be a directory.")
    started_at = timestamp()
    began = time.monotonic()
    process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    outputs = {}

    def drain(stream, name):
        digest = hashlib.sha256()
        size = 0
        while block := stream.read(65536):
            digest.update(block)
            size += len(block)
        outputs[name] = {"digest": "sha256:" + digest.hexdigest(), "bytes": size}
        stream.close()

    readers = [threading.Thread(target=drain, args=(process.stdout, "stdout")),
               threading.Thread(target=drain, args=(process.stderr, "stderr"))]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    for reader in readers:
        reader.join()
    return {"status": "timed_out" if timed_out else "exited",
            "exit_code": process.returncode, "executable": Path(command[0]).name,
            "command_digest": "sha256:" + hashlib.sha256("\0".join(command).encode()).hexdigest(),
            "cwd": str(root), "stdout": outputs["stdout"], "stderr": outputs["stderr"],
            "started_at": started_at, "completed_at": timestamp(),
            "elapsed_ms": int((time.monotonic() - began) * 1000)}
