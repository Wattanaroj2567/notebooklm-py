# MCP Server Quickstart (SSE + ChatGPT / Claude)

**Status:** Active
**Last Updated:** 2026-05-11

This document explains how to run the NotebookLM Model Context Protocol (MCP) server and connect it to **ChatGPT** (via SSE) or **Claude** (via stdio or SSE).

## Prerequisites

- Python 3.10+ **or** Docker with Docker Compose
- A NotebookLM authenticated storage state (run `notebooklm login` on the host first)
- _(For ChatGPT web / remote SSE only)_ A public HTTPS URL (Cloudflare Tunnel, ngrok, or similar)

## Installation (local Python)

Install the package with MCP support:

```bash
uv sync --extra dev --extra browser
# or: pip install -e ".[browser]"
```

## Running the server

### Option A: Local process

```bash
uv run notebooklm-mcp
# or: python scripts/run_mcp.py
```

The server listens on `http://0.0.0.0:8000` by default (`HOST` / `PORT` env vars override this).

### Option B: Docker Compose + Cloudflare (SSE / fixed public URL)

Use this when you need a public endpoint — for example ChatGPT web or Claude running on another machine.

From the repository root:

```bash
export TUNNEL_TOKEN=<your-cloudflare-tunnel-token>
docker compose up -d --build
```

- Service **mcp-auth-sync** runs first, copies your host NotebookLM auth into
  `./.notebooklm-docker`, and verifies live auth before the MCP server starts.
- Service **notebooklm-mcp** exposes port **8000** and mounts
  `./.notebooklm-docker` into the container as `/root/.notebooklm`.
- Service **mcp-tunnel** runs `cloudflared` and forwards traffic to `http://notebooklm-mcp:8000`.

After any `notebooklm login --fresh` or browser-cookie refresh while the stack
is already running, refresh the MCP auth mirror with:

```bash
scripts/sync_mcp_auth.sh
```

The MCP server detects a changed `storage_state.json` and reloads its
NotebookLM client before the next tool call. If you use Docker Desktop's UI,
running the **mcp-auth-sync** service is enough to refresh the auth mirror.

If the MCP logs say Google rejected the cookie file, verify both sides:

```bash
uv run notebooklm auth check --test --json
docker exec notebooklm-mcp sh -lc 'notebooklm auth check --test --json'
```

When the host passes but the container fails, run:

```bash
scripts/sync_mcp_auth.sh
```

Point your MCP client at the tunnel hostname with path **`/sse`** (see below).

### Health check (SSE)

The MCP stream endpoint keeps the connection open; a quick check is:

```bash
curl -N --max-time 3 http://127.0.0.1:8000/sse
```

You should see HTTP 200 and the beginning of an SSE stream (timeouts are normal).

## Smoke test (Python MCP client)

The repo file `test_mcp_client.py` exercises the **same SSE transport** ChatGPT uses:

```bash
# Local server (default)
uv run python test_mcp_client.py

# Cloudflare / public URL — full path must end with /sse
export NOTEBOOKLM_MCP_SSE_URL="https://your-host.example.com/sse"
uv run python test_mcp_client.py
```

Optional override: `uv run python test_mcp_client.py --url https://your-host.example.com/sse`

## Claude Connector

Claude supports MCP through **stdio** (local process) or **SSE** (remote). The stdio route is simplest and needs no public URL.

### Claude Code (stdio — recommended)

Register the server with a single command inside your project:

```bash
claude mcp add --transport stdio notebooklm-py -- python3 -m notebooklm.rpc.mcp_server
```

Or use the built-in skill installer:

```bash
notebooklm skill install
```

The server starts automatically when Claude Code loads and shuts down when it exits.

### Claude Desktop (stdio)

Add to your Claude Desktop configuration (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows, `~/.config/Claude/claude_desktop_config.json` on Linux):

```json
{
  "mcpServers": {
    "notebooklm": {
      "command": "python3",
      "args": ["-m", "notebooklm.rpc.mcp_server"]
    }
  }
}
```

### Claude (SSE — remote or LAN)

If you prefer SSE (for example running the server on another machine):

1. Start the server with SSE transport: `uv run notebooklm-mcp` (listens on `http://0.0.0.0:8000`).
2. In Claude Code or Claude Desktop, point the MCP client to your SSE endpoint (e.g. `http://host:8000/sse` or `https://your-host.example.com/sse`).

## Connecting to ChatGPT Web

1. Open [chatgpt.com](https://chatgpt.com).
2. Enable **Developer mode** under **Settings → Apps → Advanced settings**.
3. **Create app** (connector): set **Server URL** to your public URL with **`/sse`** appended, for example `https://your-host.example.com/sse`.
4. Authentication: typically **none** at the HTTP layer; the server uses your NotebookLM session from `notebooklm login` on the host (or mounted volume in Docker).
5. In a chat, use **Attach → Developer mode** and enable your NotebookLM connector.

## Framework Notebook

The MCP server uses a NotebookLM notebook, not local framework files, for agent roles
and workflow playbooks. Put those manuals in a normal NotebookLM notebook and expose
the notebook ID to the server:

```bash
export NOTEBOOKLM_FRAMEWORK_NOTEBOOK_ID="your-framework-notebook-id"
uv run notebooklm-mcp
```

Docker users can set the same variable in their shell or `.env` before
`docker compose up`.

Agents should call `ask_framework_manual` whenever they need AI-team role behavior,
workflow SOPs, or instructions for using NotebookLM MCP correctly. This keeps the
Framework Notebook as the source of truth and lets NotebookLM provide citations from
the underlying manuals.

## Available tools (current server)

The Python MCP server registers tools across these areas:

### Framework / workflows

| Tool                         | Purpose                                                                      |
| ---------------------------- | ---------------------------------------------------------------------------- |
| `ask_framework_manual`       | Ask the configured Framework Notebook for role playbooks and MCP workflows.  |
| `read_framework_manual`      | Deprecated shim; points agents to `ask_framework_manual`.                    |
| `run_research_team_workflow` | Create a notebook, add one URL (deduped), optional Thai summary.             |
| `run_deep_search_workflow`   | Create/reuse a notebook, start web research, return structured `next_action`. |

### Research (CLI parity)

Matches `notebooklm source add-research` and `notebooklm research status` / `research wait --import-all` on an **existing** `notebook_id`:

| Tool                       | Purpose                                                                                 |
| -------------------------- | --------------------------------------------------------------------------------------- |
| `start_research`           | Start web/drive research (`source`, `mode` same as CLI).                                |
| `poll_research_results`    | One-shot poll (`research status`).                                                      |
| `import_research_sources`  | Import after completion; omit `source_indices` to import all.                           |
| `research_wait_and_import` | Poll until complete then import every discovered source (`research wait --import-all`). |

### Notebooks

| Tool                   | Purpose                                       |
| ---------------------- | --------------------------------------------- |
| `list_notebooks`       | List notebooks.                               |
| `create_notebook`      | Create a notebook.                            |
| `get_or_create_notebook` | Reuse a matching notebook or create one.     |
| `delete_notebook`      | Dry-run/confirm/verify single notebook delete. |
| `delete_notebooks` / `delete_notebooks_by_title` | Dry-run/confirm/verify batch delete. |
| `rename_notebook`      | Rename a notebook (`new_title`).              |
| `get_notebook_summary` | Summary and suggested topics (`notebook_id`). |
| `get_share_status`     | Sharing state and collaborators.              |
| `set_notebook_public`  | Turn public link on/off.                      |

### Sources

| Tool                  | Purpose                                                                                                                                              |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_sources`        | List sources.                                                                                                                                        |
| `add_url_source`      | Add URL/YouTube (dedupe). Default **`wait=false`** (returns quickly); set `wait=true` only if you must block until indexed (often slow for YouTube). |
| `add_text_source`     | Add pasted text (title dedupe).                                                                                                                      |
| `refresh_source`      | Refresh a source.                                                                                                                                    |
| `get_source_fulltext` | Full indexed text with raw/clean counters when clean extraction is enabled.                                                                          |
| `delete_source`       | Dry-run/confirm/verify source removal.                                                                                                               |

### Studio generation

All of these return a task payload; use `poll_artifact_status` with `notebook_id` and `task_id` until complete (rate limits may apply).

| Tool                                    | Purpose                                                   |
| --------------------------------------- | --------------------------------------------------------- |
| `generate_audio_overview`               | Podcast-style audio.                                      |
| `generate_video_overview`               | Video overview.                                           |
| `generate_cinematic_video`              | Cinematic video.                                          |
| `generate_report`                       | Report (briefing, study guide, blog, custom).             |
| `generate_quiz` / `generate_flashcards` | Quiz / flashcards.                                        |
| `generate_infographic`                  | Infographic.                                              |
| `generate_slide_deck`                   | Slides.                                                   |
| `generate_data_table`                   | Data table from instructions.                             |
| `generate_mind_map`                     | Mind map (saved as a note).                               |
| `poll_artifact_status`                  | Poll a generation task; completed responses include artifact metadata and `next_action`. |
| `list_artifacts`                        | List studio artifacts with content retrieval hints.       |
| `get_artifact`                          | Metadata for one artifact by `artifact_id`.               |
| `get_artifact_content`                  | Retrieve artifact body content; supports explicit output formats. |

### Notes, chat, export

| Tool                                                                      | Purpose                                                      |
| ------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `ask_question`                                                            | RAG Q&A over notebook sources; supports strict JSON mode.    |
| `list_notes` / `create_note` / `get_note` / `rename_note` / `delete_note` | Manage user notes; delete uses dry-run/confirm/verify.       |
| `export_artifact`                                                         | Export to Google Docs or Sheets (`type`: `DOCS` / `SHEETS`). |

## Security notes

- The connector URL is effectively a **public MCP endpoint** unless you protect it (for example Cloudflare Access, IP allowlists, or a private tunnel).
- The server uses your NotebookLM credentials from disk; treat `~/.notebooklm` like a secret and keep tunnel tokens out of git.

## See also

- Repository `docker-compose.yml` for tunnel + MCP layout.
- `MCP_SKILL.md` for agent-oriented orchestration hints loaded by the server when present.
- `NOTEBOOKLM_FRAMEWORK_NOTEBOOK_ID` for the Framework Notebook queried by `ask_framework_manual`.
