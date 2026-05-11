"""Smoke-test NotebookLM MCP over SSE (same stack as ChatGPT Developer connectors).

Uses the official MCP Python client: ``sse_client`` + ``ClientSession``, then
``initialize``, ``list_tools``, and ``call_tool`` with argument names exactly as
registered in ``notebooklm.mcp_server``.

Configuration (see docs/mcp_quickstart.md):

- ``NOTEBOOKLM_MCP_SSE_URL``: full SSE URL (must end with ``/sse``). Default:
  ``http://127.0.0.1:8000/sse`` for a local ``notebooklm-mcp`` process.
- Override on the CLI: ``--url https://your-host.example.com/sse``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

DEFAULT_SSE_URL = "http://127.0.0.1:8000/sse"
ENV_SSE_URL = "NOTEBOOKLM_MCP_SSE_URL"


def _die(msg: str, code: int = 2) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def _normalize_sse_url(url: str) -> str:
    u = url.strip().rstrip("/")
    if not u.endswith("/sse"):
        _die(f"SSE URL must end with /sse (got {url!r}). " "Example: http://127.0.0.1:8000/sse")
    return u


def _print_tool_result(result: Any, label: str) -> None:
    print(f"\n--- {label} ---")
    if result.isError:
        print("Error:", result.content)
        return
    for item in result.content:
        # Check if the item is a TextContent (has 'text' attribute)
        if hasattr(item, "text"):
            print(item.text)  # type: ignore
        else:
            print(item)


def _first_notebook_id_from_list_notebooks(text: str) -> str | None:
    """Parse MCP text payload from ``list_notebooks`` (JSON array of {id, title})."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            return first.get("id")
    if isinstance(data, dict):
        return data.get("id")
    return None


async def run_smoke(sse_url: str) -> None:
    print(f"MCP SSE endpoint: {sse_url}\n")

    async with sse_client(sse_url) as streams, ClientSession(streams[0], streams[1]) as session:
        await session.initialize()
        print("OK: MCP session initialized (initialize).")

        listed = await session.list_tools()
        print(f"OK: list_tools -> {len(listed.tools)} tools.")

        print("\nCalling tool: list_notebooks (arguments: {})")
        nb_result = await session.call_tool("list_notebooks", {})
        _print_tool_result(nb_result, "list_notebooks")

        first_id: str | None = None
        if not nb_result.isError:
            for item in nb_result.content:
                if hasattr(item, "text"):
                    first_id = _first_notebook_id_from_list_notebooks(item.text)  # type: ignore
                    if first_id:
                        break

        if not first_id:
            print("\nSkip ask_question: no notebook id parsed (empty list or parse error).")
            return

        print("\nCalling tool: ask_question")
        print(f"  notebook_id: {first_id}")
        q = "สรุปสั้น ๆ ว่าโน้ตบุ๊กนี้เกี่ยวกับอะไร (ภาษาไทย 1–2 ประโยค)"
        ask_result = await session.call_tool(
            "ask_question",
            {"notebook_id": first_id, "question": q},
        )
        _print_tool_result(ask_result, "ask_question")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test NotebookLM MCP server over SSE (mcp.client.sse)."
    )
    parser.add_argument(
        "--url",
        default=os.environ.get(ENV_SSE_URL, DEFAULT_SSE_URL),
        help=f"SSE URL (default: {ENV_SSE_URL} env or {DEFAULT_SSE_URL})",
    )
    args = parser.parse_args()
    sse_url = _normalize_sse_url(args.url)
    asyncio.run(run_smoke(sse_url))


if __name__ == "__main__":
    main()
