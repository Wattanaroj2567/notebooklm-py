import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings

from notebooklm import NotebookLMClient
from notebooklm.types import artifact_status_to_str, source_status_to_str

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("notebooklm-mcp")

# Initialize FastMCP
mcp = FastMCP(
    "NotebookLM",
    instructions="Tool for interacting with Google NotebookLM notebooks, sources, and chat.",
    # Disable DNS rebinding protection so requests via ngrok/reverse proxy are accepted
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False),
)

# Global client state
_client: NotebookLMClient | None = None
_client_lock = asyncio.Lock()


async def get_client() -> NotebookLMClient:
    """Get or initialize the NotebookLM client."""
    global _client
    async with _client_lock:
        if _client is None:
            try:
                # Use a longer timeout (120s) for slow NotebookLM operations
                _client = await NotebookLMClient.from_storage(timeout=120.0)
                # Open the client connection (uses async context enter)
                await _client.__aenter__()
                logger.info("NotebookLM client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize NotebookLM client: {e}")
                raise RuntimeError(
                    "Failed to initialize NotebookLM client. "
                    "Please run `notebooklm login` on the server host first."
                ) from e
        return _client


# --- Middleware for SSE & Cloudflare ---

class SSEMiddleware(BaseHTTPMiddleware):
    """Middleware to ensure SSE responses are not buffered by Cloudflare/proxies."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Fix Cloudflare buffering for SSE
        if request.url.path == "/sse":
            response.headers["Cache-Control"] = "no-cache, no-transform"
            response.headers["X-Accel-Buffering"] = "no"
            response.headers["Connection"] = "keep-alive"
            # Ensure no chunked encoding issues with some proxies
            if "Transfer-Encoding" in response.headers:
                del response.headers["Transfer-Encoding"]
        return response


# --- Notebook Tools ---
# ... (rest of tools)


@mcp.tool()
async def list_notebooks() -> list[dict[str, Any]]:
    """List all available notebooks in the account."""
    client = await get_client()
    notebooks = await client.notebooks.list()
    return [
        {
            "id": nb.id,
            "title": nb.title,
            "last_modified": str(nb.last_modified) if hasattr(nb, "last_modified") else None,
        }
        for nb in notebooks
    ]


@mcp.tool()
async def create_notebook(title: str) -> dict[str, Any]:
    """Create a new NotebookLM notebook. Use this when the user asks to start a new project or research topic."""
    client = await get_client()
    nb = await client.notebooks.create(title)
    return {
        "id": nb.id,
        "title": nb.title,
    }


@mcp.tool()
async def delete_notebook(notebook_id: str) -> bool:
    """Delete a NotebookLM notebook by its ID. Use with extreme caution."""
    client = await get_client()
    return await client.notebooks.delete(notebook_id)


@mcp.tool()
async def get_notebook_summary(notebook_id: str) -> dict[str, Any]:
    """Get the summary and description of a specific notebook."""
    client = await get_client()
    summary = await client.notebooks.get_summary(notebook_id)
    description = await client.notebooks.get_description(notebook_id)
    return {
        "notebook_id": notebook_id,
        "summary": summary,
        "description": description.description
        if hasattr(description, "description")
        else str(description),
    }


# --- Source Tools ---


@mcp.tool()
async def list_sources(notebook_id: str) -> list[dict[str, Any]]:
    """List all sources associated with a specific notebook."""
    client = await get_client()
    sources = await client.sources.list(notebook_id)
    return [
        {
            "id": s.id,
            "title": s.title,
            "type": s.kind.value if hasattr(s.kind, "value") else str(s.kind),
            "status": source_status_to_str(s.status),
        }
        for s in sources
    ]


@mcp.tool()
async def add_url_source(notebook_id: str, url: str) -> dict[str, Any]:
    """Add a new URL source (web page, YouTube) to a notebook."""
    client = await get_client()
    source = await client.sources.add_url(notebook_id, url)
    return {
        "id": source.id,
        "title": source.title,
        "status": source_status_to_str(source.status),
    }


@mcp.tool()
async def add_text_source(notebook_id: str, title: str, text: str) -> dict[str, Any]:
    """Add a new text source (raw text) to a notebook."""
    client = await get_client()
    source = await client.sources.add_text(notebook_id, title, text)
    return {
        "id": source.id,
        "title": source.title,
        "status": source_status_to_str(source.status),
    }


@mcp.tool()
async def delete_source(notebook_id: str, source_id: str) -> bool:
    """Delete a source from a notebook."""
    client = await get_client()
    return await client.sources.delete(notebook_id, source_id)


# --- Note Tools ---


@mcp.tool()
async def list_notes(notebook_id: str) -> list[dict[str, Any]]:
    """List all user-created notes in a notebook."""
    client = await get_client()
    notes = await client.notes.list(notebook_id)
    return [
        {
            "id": n.id,
            "title": n.title,
            "last_modified": str(n.last_modified) if hasattr(n, "last_modified") else None,
        }
        for n in notes
    ]


@mcp.tool()
async def create_note(notebook_id: str, title: str, content: str) -> dict[str, Any]:
    """Create a new note with a title and content."""
    client = await get_client()
    note = await client.notes.create(notebook_id, title, content)
    return {
        "id": note.id,
        "title": note.title,
    }


@mcp.tool()
async def get_note(notebook_id: str, note_id: str) -> dict[str, Any]:
    """Get the full content of a specific note."""
    client = await get_client()
    note = await client.notes.get(notebook_id, note_id)
    if not note:
        raise ValueError(f"Note {note_id} not found")
    return {
        "id": note.id,
        "title": note.title,
        "content": note.content,
    }


@mcp.tool()
async def delete_note(notebook_id: str, note_id: str) -> bool:
    """Delete a note."""
    client = await get_client()
    return await client.notes.delete(notebook_id, note_id)


# --- Artifact Tools ---


@mcp.tool()
async def list_artifacts(notebook_id: str) -> list[dict[str, Any]]:
    """List all AI-generated artifacts (Audio, Reports, etc.) in a notebook."""
    client = await get_client()
    artifacts = await client.artifacts.list(notebook_id)
    return [
        {
            "id": a.id,
            "title": a.title,
            "kind": a.kind.value if hasattr(a.kind, "value") else str(a.kind),
            "status": artifact_status_to_str(a.status),
            "created_at": str(a.created_at) if a.created_at else None,
        }
        for a in artifacts
    ]


@mcp.tool()
async def generate_audio_overview(
    notebook_id: str, instructions: str | None = None
) -> dict[str, Any]:
    """Generate a podcast-style Audio Overview."""
    client = await get_client()
    status = await client.artifacts.generate_audio(notebook_id, instructions=instructions)
    return {"task_id": status.task_id, "status": status.status}


@mcp.tool()
async def generate_study_guide(notebook_id: str) -> dict[str, Any]:
    """Generate a Study Guide report."""
    client = await get_client()
    status = await client.artifacts.generate_study_guide(notebook_id)
    return {"task_id": status.task_id, "status": status.status}


@mcp.tool()
async def poll_artifact_status(notebook_id: str, task_id: str) -> dict[str, Any]:
    """Check the status of a generation task."""
    client = await get_client()
    status = await client.artifacts.poll_status(notebook_id, task_id)
    return {
        "task_id": status.task_id,
        "status": status.status,
        "is_complete": status.is_complete,
        "is_failed": status.is_failed,
        "error": status.error,
    }


@mcp.tool()
async def delete_artifact(notebook_id: str, artifact_id: str) -> bool:
    """Delete an artifact."""
    client = await get_client()
    return await client.artifacts.delete(notebook_id, artifact_id)


# --- Research Tools ---


@mcp.tool()
async def start_research(
    notebook_id: str, query: str, source: str = "web", mode: str = "fast"
) -> dict[str, Any]:
    """Search the web or Drive for new information."""
    client = await get_client()
    task = await client.research.start(notebook_id, query, source=source, mode=mode)
    return task if task else {"error": "Failed to start research"}


@mcp.tool()
async def poll_research_results(notebook_id: str) -> dict[str, Any]:
    """Get results and found sources from a research task."""
    client = await get_client()
    return await client.research.poll(notebook_id)


@mcp.tool()
async def import_research_sources(
    notebook_id: str, task_id: str, source_indices: list[int]
) -> list[dict[str, str]]:
    """Import discovered research sources into your notebook."""
    client = await get_client()
    results = await client.research.poll(notebook_id)
    if results["status"] == "no_research" or not results.get("sources"):
        raise ValueError("No research sources found to import")

    selected_sources = [
        results["sources"][i] for i in source_indices if 0 <= i < len(results["sources"])
    ]
    if not selected_sources:
        raise ValueError("No valid sources selected")

    imported = await client.research.import_sources(notebook_id, task_id, selected_sources)
    return [{"id": s["id"], "title": s["title"]} for s in imported]


# --- Sharing Tools ---


@mcp.tool()
async def get_share_status(notebook_id: str) -> dict[str, Any]:
    """Check current sharing settings and user access."""
    client = await get_client()
    status = await client.sharing.get_status(notebook_id)
    return {
        "notebook_id": status.notebook_id,
        "is_public": status.is_public,
        "share_url": status.share_url,
        "shared_users": [
            {"email": u.email, "permission": u.permission.name} for u in status.shared_users
        ],
    }


@mcp.tool()
async def set_notebook_public(notebook_id: str, public: bool) -> dict[str, Any]:
    """Enable or disable public link sharing."""
    client = await get_client()
    status = await client.sharing.set_public(notebook_id, public)
    return {"is_public": status.is_public, "share_url": status.share_url}


# --- Chat Tool ---


@mcp.tool()
async def ask_question(notebook_id: str, question: str) -> str:
    """Ask a question based on a notebook's sources."""
    client = await get_client()
    result = await client.chat.ask(notebook_id, question)
    return getattr(result, "answer", None) or getattr(result, "answer_text", None) or str(result)


# --- FastAPI & SSE Transport ---

@asynccontextmanager
async def lifespan(application: FastAPI):
    """FastAPI lifespan: clean up the NotebookLM client on shutdown."""
    yield
    global _client
    if _client:
        await _client.__aexit__(None, None, None)
        _client = None
        logger.info("NotebookLM client closed")


# Mount the FastMCP SSE app under the root path.
# mcp.sse_app() returns a Starlette ASGI app that handles /sse and /messages/
# using the raw ASGI interface (scope, receive, send) — compatible with mcp >=1.20.
app = FastAPI(title="NotebookLM MCP Server", lifespan=lifespan)

# Add SSE fix middleware
app.add_middleware(SSEMiddleware)

logger.info("ℹ️ ChatGPT-compatible mode: no custom API key auth. "
            "Ensure the server is not exposed to untrusted networks.")

app.mount("/", app=mcp.sse_app())


def main():
    """Entry point for the MCP server."""
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    logger.info(f"Starting NotebookLM MCP server on {host}:{port}")
    # proxy_headers=True and forwarded_allow_ips="*" are critical for Cloudflare Tunnel
    uvicorn.run(
        app,
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        timeout_keep_alive=65
    )


if __name__ == "__main__":
    main()
