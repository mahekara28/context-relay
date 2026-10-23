"""Offline example: preserve source rows through one optional argument repair.

Run: python examples/protected_export.py
No model, server, download or API key. The deterministic callback represents the
host's repair step; a real callback can use an already configured model instead.
"""
from pathlib import Path
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from context_relay import AutomaticMemory, ContentPolicy, ContextGuard


def run(output_dir):
    source_rows = [{"item": "Folders", "units": 4, "unit_price_minor": 125},
                   {"item": "Tape", "units": 2, "unit_price_minor": 250}]
    content = ContentPolicy({"rows": source_rows}, exact=("/rows",))
    output_path = Path(output_dir) / "report.json"
    writes = []
    with ContextGuard(name="Protected export") as guard:
        memory = AutomaticMemory(guard.store, guard.session_id)
        memory.process([{"role": "user", "content": "Export as JSON."},
                        {"role": "user", "content": "Reply in Hindi."}])
        guard.allow_tool("write_export", content=content,
                         memory_bindings={"format": "export_format", "language": "response_language"},
                         optional_args=("summary",))

        def write_export(tool, arguments):
            assert tool == "write_export"
            output_path.write_text(json.dumps(arguments, ensure_ascii=False, indent=2), encoding="utf-8")
            writes.append(str(output_path))
            return {"path": str(output_path)}

        # Correct settings, but an unauthorized change inside the data.
        wrong = {"format": "JSON", "language": "Hindi", "summary": "\u0930\u093f\u092a\u094b\u0930\u094d\u091f \u0924\u0948\u092f\u093e\u0930 \u0939\u0948\u0964",
                 "rows": [{**source_rows[0], "item": "Translated Folders"}, source_rows[1]]}
        first = guard.turn().execute_with_recovery("write_export", wrong, write_export)
        assert first["status"] == "blocked" and not output_path.exists()

        # The optional repair changes only the proposal, then passes the same checks.
        def repair(request):
            return {**request["arguments"], "rows": source_rows}

        result = guard.turn().execute_with_recovery("write_export", wrong, write_export, repair=repair)
        saved = json.loads(output_path.read_text(encoding="utf-8"))
        assert result["status"] == "repaired" and result["repair_attempts"] == 1
        assert len(writes) == 1 and saved["rows"] == source_rows
        return {"first_proposal": first["status"], "recovery": result["status"],
                "writes": len(writes), "source_rows_preserved": True,
                "protected_paths": ["/rows"], "summary_meaning_checked": False,
                "model_calls": 0}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="relay-protected-export-") as directory:
        print(json.dumps(run(directory), indent=2))
