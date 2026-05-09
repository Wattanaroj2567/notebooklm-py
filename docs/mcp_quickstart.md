# MCP Server Quickstart (SSE + ChatGPT)

This document explains how to run the NotebookLM Model Context Protocol (MCP) server and connect it to the ChatGPT web interface using Server-Sent Events (SSE).

## Prerequisites

- Python 3.10+
- A NotebookLM authenticated storage state (run `notebooklm login` first)
- `ngrok` or similar tunnel for exposing local server to the web

## Installation

Install the package with MCP support:

```bash
pip install -e .
pip install mcp fastapi uvicorn
```

## Running the Server

1. **Start the MCP Server:**

   ```bash
   python scripts/run_mcp.py
   ```
   The server will start on `http://0.0.0.0:8000`.

2. **Expose with ngrok:**

   In another terminal, expose the port 8000:
   ```bash
   ngrok http 8000
   ```
   Copy the `https://` forwarding URL (e.g., `https://random-id.ngrok-free.app`).

## Connecting to ChatGPT Web

1. **Open ChatGPT:** Go to [chatgpt.com](https://chatgpt.com).
2. **Enable Developer Mode:**
   - Go to **Settings** -> **Apps** -> **Advanced settings**.
   - Toggle **Developer mode** to ON.
3. **Add New App:**
   - Click **Create app** (or **New Connector**).
   - Enter a name (e.g., "NotebookLM").
   - **Server URL:** Paste your ngrok URL followed by `/sse` (e.g., `https://random-id.ngrok-free.app/sse`).
   - **Authentication:** Select "No Authentication" (the server relies on your local `notebooklm login` state).
4. **Activate in Chat:**
   - Start a new chat.
   - Click the **+** (Attach) icon.
   - Select **Developer mode**.
   - Toggle your **NotebookLM** app to ON.

## Available Tools

Once connected, ChatGPT can use the following tools:

### Notebooks
- `list_notebooks`: List all your NotebookLM notebooks.
- `get_notebook_summary`: Get the summary and description of a notebook.

### Sources
- `list_sources`: List all sources in a notebook.
- `add_url_source`: Add a new URL source to a notebook.
- `add_text_source`: Add a new text source (raw text) to a notebook.
- `delete_source`: Remove a source from a notebook.

### Notes
- `list_notes`: List all user-created notes in a notebook.
- `create_note`: Create a new note with a title and content.
- `get_note`: Read the content of a specific note.
- `delete_note`: Delete a note.

### Artifacts (AI Content)
- `list_artifacts`: List all AI-generated content (Audio, Study Guides, etc.).
- `generate_audio_overview`: Start generating a podcast-style Audio Overview.
- `generate_study_guide`: Start generating a Study Guide report.
- `poll_artifact_status`: Check if a generation task is complete or failed.
- `delete_artifact`: Delete an artifact.

### Research
- `start_research`: Search the web or Google Drive for new information.
- `poll_research_results`: Get the status and found sources of a research task.
- `import_research_sources`: Add discovered research sources into your notebook.

### Sharing
- `get_share_status`: Check current sharing settings and user access.
- `set_notebook_public`: Enable or disable public link sharing.

### Chat
- `ask_question`: Ask a question based on a specific notebook's sources.

## Security Notes

- The server uses your local authentication tokens.
- Using `ngrok` exposes your server to the internet. Close the tunnel when not in use.
- ChatGPT will send requests to your server; ensure you trust the environment where it's running.
