# MCP add_import_file Probe

วันที่ทดสอบ: 2026-05-16

## Purpose
This file was written via `add_import_file` to verify that ChatGPT-generated content can be written into the MCP import root and added to NotebookLM as a source without manually copying files into `./mcp_imports`.

## Expected Result
- The file should be written under `/imports/mcp_add_import_file_probe.md` on the MCP server.
- The same file should be added as a NotebookLM source.
- `list_sources` should show this source with status `ready` or `processing`.
