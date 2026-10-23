"""Local connect/register/bind example; no model, credentials or network needed.

The provider stand-in deliberately proposes the wrong format. Relay repairs the
known setting, checks the protected data, and writes exactly one local JSON file.
"""

import json
import tempfile
from pathlib import Path

from context_relay import ContentPolicy, MemoryPolicy, ToolRegistry, connect


SOURCE = {"rows": [{"item": "Folders", "quantity": 4}, {"item": "Tape", "quantity": 2}]}


def provider_stand_in(*, messages):
    return {"tool": "write_export", "arguments": {
        "format": "CSV", "language": "Hindi", "rows": SOURCE["rows"],
        "summary": "\u0924\u0948\u092f\u093e\u0930",
    }}


def main():
    with tempfile.TemporaryDirectory() as directory, connect(provider_stand_in, memory=MemoryPolicy()) as agent:
        path = Path(directory) / "export.json"
        registry = ToolRegistry(agent)
        writes = []

        @registry.tool(memory=True, content=ContentPolicy(SOURCE, exact=("/rows",)), unchecked=("summary",))
        def write_export(format, language, rows, summary=""):
            path.write_text(json.dumps({"rows": rows, "summary": summary}, ensure_ascii=False), encoding="utf-8")
            writes.append(path)
            return {"written": True, "format": format, "language": language}

        result = agent.invoke(messages=[{"role": "user", "content": "Export as JSON. Reply in Hindi."}])
        proposal = result.response
        action = registry.bind(result.prepared).execute_with_recovery(
            proposal["tool"], proposal["arguments"], repair="known",
        )
        assert action["executed"] and len(writes) == 1
        assert json.loads(path.read_text(encoding="utf-8"))["rows"] == SOURCE["rows"]
        print(json.dumps({
            "status": action["status"], "writes": len(writes),
            "repair_callback_calls": action["repair_callback_calls"],
            "source_rows_preserved": True, "model_calls": 0,
            "coverage": registry.coverage(["write_export"]),
        }, indent=2))


if __name__ == "__main__":
    main()
