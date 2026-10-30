"""Deliberately stale starting implementation for a coding handoff fixture."""

import csv
from pathlib import Path


def export_report(rows, destination, *, format="CSV"):
    if format != "CSV":
        raise ValueError("Only CSV reports are supported")
    destination = Path(destination)
    with destination.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["region", "gross_revenue"])
        for row in rows:
            writer.writerow([row["region"], row["amount"]])
    return destination
