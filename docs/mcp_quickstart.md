# MCP Server Quickstart (SSE + ChatGPT)

This document explains how to run the NotebookLM Model Context Protocol (MCP) server and connect it to the ChatGPT web interface using Server-Sent Events (SSE).

## Prerequisites

- Python 3.10+ **or** Docker with Docker Compose
- A NotebookLM authenticated storage state (run `notebooklm login` on the host first)
- A public HTTPS URL for ChatGPT to reach your server (Cloudflare Tunnel, ngrok, or similar)

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

### Option B: Docker Compose + Cloudflare (recommended for a fixed URL)

From the repository root:

```bash
export TUNNEL_TOKEN=<your-cloudflare-tunnel-token>
docker compose up -d --build
```

- Service **notebooklm-mcp** exposes port **8000** and mounts `~/.notebooklm` into the container as `/root/.notebooklm` so the same login session as on your machine is used.
- Service **mcp-tunnel** runs `cloudflared` and forwards traffic to `http://notebooklm-mcp:8000`.

Point ChatGPT at your tunnel hostname with path **`/sse`** (see below).

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

## Connecting to ChatGPT Web

1. Open [chatgpt.com](https://chatgpt.com).
2. Enable **Developer mode** under **Settings → Apps → Advanced settings**.
3. **Create app** (connector): set **Server URL** to your public URL with **`/sse`** appended, for example `https://your-host.example.com/sse`.
4. Authentication: typically **none** at the HTTP layer; the server uses your NotebookLM session from `notebooklm login` on the host (or mounted volume in Docker).
5. In a chat, use **Attach → Developer mode** and enable your NotebookLM connector.

## Available tools (current server)

The Python MCP server registers **40** tools. Summary by area:

### Framework / workflows

| Tool | Purpose |
|------|---------|
| `read_framework_manual` | Read NotebookLM `ai_workspace` manuals (`strategy` / `quiz` / `study`). |
| `run_research_team_workflow` | Create a notebook, add one URL (deduped), optional Thai summary. |
| `run_deep_search_workflow` | Create a notebook, run web research, auto-import results, summarize in Thai. |

### Research (CLI parity)

Matches `notebooklm source add-research` and `notebooklm research status` / `research wait --import-all` on an **existing** `notebook_id`:

| Tool | Purpose |
|------|---------|
| `start_research` | Start web/drive research (`source`, `mode` same as CLI). |
| `poll_research_results` | One-shot poll (`research status`). |
| `import_research_sources` | Import after completion; omit `source_indices` to import all. |
| `research_wait_and_import` | Poll until complete then import every discovered source (`research wait --import-all`). |

### Notebooks

| Tool | Purpose |
|------|---------|
| `list_notebooks` | List notebooks. |
| `create_notebook` | Create a notebook. |
| `delete_notebook` | Delete a notebook. |
| `rename_notebook` | Rename a notebook (`new_title`). |
| `get_notebook_summary` | Summary and suggested topics (`notebook_id`). |
| `get_share_status` | Sharing state and collaborators. |
| `set_notebook_public` | Turn public link on/off. |

### Sources

| Tool | Purpose |
|------|---------|
| `list_sources` | List sources. |
| `add_url_source` | Add URL/YouTube (dedupe). Default **`wait=false`** (returns quickly); set `wait=true` only if you must block until indexed (often slow for YouTube). |
| `add_text_source` | Add pasted text (title dedupe). |
| `refresh_source` | Refresh a source. |
| `get_source_fulltext` | Full indexed text. |
| `delete_source` | Remove a source. |

### Studio generation

All of these return a task payload; use `poll_artifact_status` with `notebook_id` and `task_id` until complete (rate limits may apply).

| Tool | Purpose |
|------|---------|
| `generate_audio_overview` | Podcast-style audio. |
| `generate_video_overview` | Video overview. |
| `generate_cinematic_video` | Cinematic video. |
| `generate_report` | Report (briefing, study guide, blog, custom). |
| `generate_quiz` / `generate_flashcards` | Quiz / flashcards. |
| `generate_infographic` | Infographic. |
| `generate_slide_deck` | Slides. |
| `generate_data_table` | Data table from instructions. |
| `generate_mind_map` | Mind map (saved as a note). |
| `poll_artifact_status` | Poll a generation task. |
| `list_artifacts` | List studio artifacts (audio, report, quiz, mind map, …). |
| `get_artifact` | Metadata for one artifact by `artifact_id`. |

### Notes, chat, export

| Tool | Purpose |
|------|---------|
| `ask_question` | RAG Q&A over notebook sources. |
| `list_notes` / `create_note` / `get_note` / `rename_note` / `delete_note` | Manage user notes. |
| `export_artifact` | Export to Google Docs or Sheets (`type`: `DOCS` / `SHEETS`). |

## Security notes

- The connector URL is effectively a **public MCP endpoint** unless you protect it (for example Cloudflare Access, IP allowlists, or a private tunnel).
- The server uses your NotebookLM credentials from disk; treat `~/.notebooklm` like a secret and keep tunnel tokens out of git.

## See also

- Repository `docker-compose.yml` for tunnel + MCP layout.
- `MCP_SKILL.md` for agent-oriented orchestration hints loaded by the server when present.
