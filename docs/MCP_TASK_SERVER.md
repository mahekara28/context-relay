# Local task MCP server

The optional server uses the official MCP Python SDK v2 and stdio transport.
Install with Python 3.11 or newer using `pip install '.[mcp]'`, then run
`context-relay mcp --db /absolute/path/tasks.db --subject my-harness`.

The local database and its parent directory must be private to the OS user.
`--subject` identifies a local harness inside that trust boundary; it is not
remote authentication. The host must launch the process and control its
arguments. MCP tools provide explicit reads and writes; merely registering the
server does not inject task context into a model or verify its actions.

The core package keeps Python 3.10 support and has no runtime dependencies.
The `mcp` extra is optional because the SDK requires Python 3.11 or newer.

SDK reference: https://github.com/modelcontextprotocol/python-sdk/tree/main/docs

## Claude Code project connection

From this checkout, run `context-relay connector claude configure --root .`
using the Python environment where `context-relay-local[mcp]` is installed.
This writes only this project's `.mcp.json`; it does not edit user or global
Claude settings. Run `context-relay connector claude diagnose --root .` to
inspect the entry and executable path, or `connector claude remove` to remove
only Relay's entry. The generated file is ignored by Git because it contains
absolute local paths. Claude Code may require project server approval on first
use. See the [Claude Code MCP project-scope documentation](https://code.claude.com/docs/en/mcp#project-scope).
