"""Deterministic bounded packets for cross-harness task continuation."""

from __future__ import annotations

from .models import BudgetExceeded, encode
from .task_contract import validate_task_snapshot


def compile_task_packet(snapshot: dict, *, max_chars: int = 16000) -> dict:
    """Keep authoritative current work; disclose every optional item omitted by budget."""
    if type(max_chars) is not int or not 512 <= max_chars <= 1_000_000:
        raise ValueError("max_chars must be an integer from 512 through 1000000.")
    task = validate_task_snapshot(snapshot)
    packet = {
        "packet_version": "task-packet/v1alpha1", "task_id": task["id"],
        "revision": task["revision"], "status": task["status"],
        "goal": task["goal"], "completion_criteria": task["completion_criteria"],
        "workspace": task["workspace"],
        "instructions": [item for item in task["instructions"] if item["status"] == "active"],
        "open_work": task["open_work"], "artifacts": [], "evidence": [],
        "omitted": {"artifacts": 0, "evidence": 0},
    }
    required_size = len(encode(packet))
    if required_size > max_chars:
        raise BudgetExceeded(required_size, max_chars)
    # Preserve insertion order and prioritize artifact state over optional claims.
    for field in ("artifacts", "evidence"):
        for item in task[field]:
            packet[field].append(item)
            if len(encode(packet)) > max_chars:
                packet[field].pop()
                packet["omitted"][field] += 1
    # Omitted counters can increase encoded size at decimal boundaries.
    while len(encode(packet)) > max_chars:
        field = "evidence" if packet["evidence"] else "artifacts"
        if not packet[field]:
            raise BudgetExceeded(len(encode(packet)), max_chars)
        packet[field].pop()
        packet["omitted"][field] += 1
    return packet
