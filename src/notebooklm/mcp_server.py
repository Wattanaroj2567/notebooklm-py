import asyncio
import base64
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

import uvicorn
from anyio import ClosedResourceError
from fastapi import FastAPI

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Icon
from notebooklm import NotebookLMClient
from notebooklm.exceptions import ValidationError
from notebooklm.rpc import (
    AudioFormat,
    AudioLength,
    ExportType,
    InfographicDetail,
    InfographicOrientation,
    InfographicStyle,
    QuizDifficulty,
    QuizQuantity,
    ReportFormat,
    SlideDeckFormat,
    SlideDeckLength,
    VideoFormat,
    VideoStyle,
)
from notebooklm.types import Artifact, ShareStatus, source_status_to_str

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("notebooklm-mcp")

# --- NotebookLM AI Framework Configuration ---
AI_WORKSPACE_DIR = Path(__file__).parent.parent.parent / "ai_workspace"
if not AI_WORKSPACE_DIR.exists():
    AI_WORKSPACE_DIR = Path("/app/ai_workspace")


# --- Advanced Deduplication Helpers ---


def normalize_url(url: str) -> str:
    """Normalize URL to prevent duplicates."""
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        if "youtube.com" in netloc or "youtu.be" in netloc:
            return url.strip()
        return f"{parsed.scheme}://{netloc}{path}".strip()
    except Exception:
        return url.strip()


def extract_youtube_id(url: str) -> str | None:
    """Extract YouTube video ID from URL."""
    try:
        parsed = urlparse(url)
        if parsed.hostname == "youtu.be":
            return parsed.path[1:]
        if parsed.hostname in ("www.youtube.com", "youtube.com"):
            if parsed.path == "/watch":
                return parse_qs(parsed.query).get("v", [None])[0]
            if parsed.path.startswith(("/embed/", "/v/", "/shorts/", "/live/")):
                return parsed.path.split("/")[2]
    except Exception:
        pass
    return None


async def find_existing_source(client: NotebookLMClient, notebook_id: str, url: str) -> Any | None:
    """Find existing source by URL, YouTube ID, or Normalized URL."""
    sources = await client.sources.list(notebook_id)
    target_url = normalize_url(url)
    target_yt_id = extract_youtube_id(url)

    for s in sources:
        if not s.url:
            continue
        if s.url == url or normalize_url(s.url) == target_url:
            return s
        if target_yt_id:
            existing_yt_id = extract_youtube_id(s.url)
            if existing_yt_id == target_yt_id:
                return s
    return None


async def find_existing_title(
    client: NotebookLMClient, notebook_id: str, title: str, kind: str = "source"
) -> bool:
    """Check if a source or note with the same title already exists."""
    items: list[Any]
    if kind == "source":
        items = await client.sources.list(notebook_id)
    else:
        items = await client.notes.list(notebook_id)
    return any(item.title and item.title.lower() == title.lower() for item in items)


def _artifact_to_dict(artifact: Artifact) -> dict[str, Any]:
    kind = artifact.kind
    kind_str = kind.value if hasattr(kind, "value") else str(kind)
    created = artifact.created_at.isoformat() if artifact.created_at else None
    return {
        "id": artifact.id,
        "title": artifact.title,
        "kind": kind_str,
        "status": artifact.status,
        "url": artifact.url,
        "created_at": created,
    }


def _share_status_to_dict(status: ShareStatus) -> dict[str, Any]:
    users: list[dict[str, Any]] = []
    for u in status.shared_users:
        perm = u.permission.name if hasattr(
            u.permission, "name") else str(u.permission)
        users.append(
            {
                "email": u.email,
                "permission": perm,
                "display_name": u.display_name,
            }
        )
    access = status.access.name if hasattr(
        status.access, "name") else str(status.access)
    view = status.view_level.name if hasattr(
        status.view_level, "name") else str(status.view_level)
    return {
        "notebook_id": status.notebook_id,
        "is_public": status.is_public,
        "access": access,
        "view_level": view,
        "share_url": status.share_url,
        "shared_users": users,
    }


# Load advanced instructions from MCP_SKILL.md if available
default_instructions = """You are the Orchestrator (Indy) of the NotebookLM AI Agent Framework.
CRITICAL RULES FOR RESEARCH & QUALITY:
1. **DEEP RESEARCH (new notebook)**: Use `run_deep_search_workflow` to create a notebook, run web research, auto-import, and summarize in Thai.
2. **RESEARCH (existing notebook, CLI parity)**: Use `start_research`, then `poll_research_results` or `research_wait_and_import`, then `import_research_sources` if you imported only a subset manually.
3. **SINGLE-URL INGEST**: Use `run_research_team_workflow` when the user gives one primary URL/YouTube link and you want a notebook plus optional Thai summary.
4. **DEDUPLICATION**: Source and note tools block obvious duplicates; still avoid redundant adds when you know the ID already.
5. **THAI FIRST**: Summarize research in Thai unless the user asks for another language.
6. **POLLING**: After any studio generation tool, use `poll_artifact_status` until the task completes."""

try:
    skill_path = Path(__file__).parent.parent.parent / "MCP_SKILL.md"
    if not skill_path.exists():
        skill_path = Path("/app/MCP_SKILL.md")

    if skill_path.exists():
        skill_content = skill_path.read_text(encoding="utf-8")
        default_instructions = (
            f"{skill_content}\n\n=== ADDITIONAL CONTEXT ===\n{default_instructions}"
        )
        logger.info(
            "✅ Successfully loaded advanced AI instructions from MCP_SKILL.md")
except Exception as e:
    logger.warning(f"⚠️ Could not load MCP_SKILL.md. Error: {e}")

mcp = FastMCP(
    "NotebookLM-Agent-Framework",
    instructions=default_instructions,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False),
    # Mount the Streamable HTTP handler at "/" internally.
    # The proxy will handle prefix stripping.
    streamable_http_path="/",
)

# Setup custom icon
try:
    # Check current directory, then parent, then Docker app dir
    icon_paths_to_try = [
        Path("notebooklm-py.png"),
        Path(__file__).parent.parent.parent / "notebooklm-py.png",
        Path("/app/notebooklm-py.png"),
    ]

    loaded_icon = False
    for p in icon_paths_to_try:
        if p.exists():
            b64_icon = base64.b64encode(p.read_bytes()).decode("utf-8")
            data_uri = f"data:image/png;base64,{b64_icon}"
            mcp._mcp_server.icons = [Icon(src=data_uri, mimeType="image/png")]
            logger.info(f"✅ Successfully loaded custom MCP icon from {p}")
            loaded_icon = True
            break

    if not loaded_icon:
        logger.warning(
            f"⚠️ Could not find notebooklm-py.png in any expected locations: {icon_paths_to_try}"
        )

except Exception as e:
    logger.error(
        f"❌ Failed to load custom MCP icon. Error: {e}", exc_info=True)
# Global client state
_client: NotebookLMClient | None = None
_client_lock = asyncio.Lock()


async def get_client() -> NotebookLMClient:
    global _client
    async with _client_lock:
        if _client is None:
            try:
                _client = await NotebookLMClient.from_storage(timeout=120.0)
                await _client.__aenter__()
                logger.info("NotebookLM client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize client: {e}")
                raise RuntimeError("NotebookLM client not ready.") from e
        return _client


# --- Framework Tools ---


@mcp.tool()
async def read_framework_manual(topic: Literal["strategy", "quiz", "study"] = "strategy") -> str:
    """Read AI Framework manuals from ai_workspace. (ReadOnly)"""
    file_map = {
        "strategy": "master_automation_manual.md",
        "quiz": "quiz.md",
        "study": "study_guide.md",
    }
    manual_path = AI_WORKSPACE_DIR / \
        file_map.get(topic, "master_automation_manual.md")
    return manual_path.read_text(encoding="utf-8") if manual_path.exists() else "Manual not found."


@mcp.tool()
async def run_research_team_workflow(url: str, title: str) -> dict[str, Any]:
    """Direct URL Research Workflow: Minnie->Indy->Vera->Reas->Day. (Write)"""
    client = await get_client()
    nb = await client.notebooks.create(title)
    existing = await find_existing_source(client, nb.id, url)
    if existing:
        return {
            "notebook_id": nb.id,
            "source_id": existing.id,
            "status": "ready",
            "workflow": "Duplicate found",
        }

    source = await client.sources.add_url(nb.id, url)
    try:
        await client.sources.wait_until_ready(nb.id, source.id, timeout=90)
        status = "ready"
    except Exception:
        status = "processing_background"

    summary = "Workflow executed."
    if status == "ready":
        result = await client.chat.ask(
            nb.id,
            "สรุปเนื้อหาแหล่งข้อมูลนี้เป็นภาษาไทยทั้งหมดเท่านั้น "
            "(ห้ามตอบเป็นภาษาฝรั่งเศส อังกฤษ หรือภาษาอื่น). "
            "ถ้าเป็นคำศัพท์เฉพาะให้คงศัพท์แล้วอธิบายเป็นภาษาไทย.",
        )
        summary = getattr(result, "answer", str(result))
    return {
        "notebook_id": nb.id,
        "source_id": source.id,
        "status": status,
        "initial_summary": summary,
    }


@mcp.tool()
async def run_deep_search_workflow(query: str, notebook_title: str) -> dict[str, Any]:
    """
    PERFORM WEB SEARCH & AUTO-IMPORT:
    1. Create Notebook
    2. Search Web (Indy)
    3. Wait & Automatically Import found sources (Vera)
    4. Summarize all imported data in Thai (Reas/Day)
    (Write)
    """
    client = await get_client()
    nb = await client.notebooks.create(notebook_title)

    # 1. Start Research
    task = await client.research.start(nb.id, query)
    if not task:
        return {"error": "Failed to start research"}

    # 2. Wait for Discovery (Polling)
    results = None
    for _ in range(10):  # Max 10 attempts (approx 30s)
        await asyncio.sleep(3)
        results = await client.research.poll(nb.id)
        st = results.get("status") if results else None
        if st in ("completed", "ready") and results.get("sources"):
            break

    if not results or not results.get("sources"):
        return {"notebook_id": nb.id, "status": "no_results_found"}

    # 3. AUTO-IMPORT all found sources
    found_sources = results["sources"]
    task_id = task.get("task_id") if isinstance(
        task, dict) else getattr(task, "task_id", None)
    if not task_id:
        return {"error": "Could not identify research task ID"}

    imported = await client.research.import_sources(nb.id, task_id, found_sources)

    # 4. Final Polish & Summary
    await asyncio.sleep(5)  # Brief wait for initial indexing
    result = await client.chat.ask(
        nb.id,
        f"จากแหล่งข้อมูล {len(imported)} แห่งที่เกี่ยวกับคำถามนี้: {query!r}\n"
        "ให้สรุปประเด็นสำคัญเป็นภาษาไทยทั้งหมดเท่านั้น "
        "(ห้ามตอบเป็นภาษาฝรั่งเศส อังกฤษ หรือภาษาอื่น). "
        "จัดย่อหน้าให้อ่านง่าย ระบุชื่อแหล่งหรือบริบทเมื่อจำเป็น.",
    )
    summary = getattr(result, "answer", str(result))

    return {
        "notebook_id": nb.id,
        "sources_imported": len(imported),
        "summary": summary,
        "workflow": "Discover -> Auto-Import -> Summary COMPLETED",
    }


# --- Research (CLI parity: ``source add-research``, ``research status/wait``) ---


@mcp.tool()
async def start_research(
    notebook_id: str,
    query: str,
    source: Literal["web", "drive"] = "web",
    mode: Literal["fast", "deep"] = "fast",
) -> dict[str, Any]:
    """Start web/drive research in an existing notebook (CLI: ``source add-research --no-wait``). (Write)"""
    client = await get_client()
    try:
        task = await client.research.start(notebook_id, query, source, mode)
    except ValidationError as e:
        return {"error": str(e)}
    if not task:
        return {"error": "Failed to start research"}
    return task


@mcp.tool()
async def poll_research_results(notebook_id: str) -> dict[str, Any]:
    """Poll research status and discovered sources (CLI: ``research status``). (ReadOnly)"""
    client = await get_client()
    return await client.research.poll(notebook_id)


@mcp.tool()
async def import_research_sources(
    notebook_id: str,
    task_id: str,
    source_indices: list[int] | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Import sources from a completed research task (CLI: manual pick after ``research wait``).

    Call ``poll_research_results`` first. If ``source_indices`` is omitted, imports all
    discovered sources for the task. Indices refer to the order in ``sources`` from the poll.
    (Write)
    """
    client = await get_client()
    results = await client.research.poll(notebook_id)
    if results.get("status") not in ("completed", "ready"):
        return {
            "error": "Research not ready to import",
            "status": results.get("status"),
        }
    if results.get("task_id") != task_id:
        return {
            "error": "task_id does not match the latest completed research for this notebook",
            "expected_task_id": results.get("task_id"),
        }
    sources = list(results.get("sources") or [])
    if not sources:
        return {"error": "No sources to import"}
    if source_indices is not None and len(source_indices) > 0:
        picked: list[Any] = []
        for i in source_indices:
            if isinstance(i, int) and 0 <= i < len(sources):
                picked.append(sources[i])
        if not picked:
            return {"error": "No valid source_indices"}
        sources = picked
    try:
        return await client.research.import_sources(notebook_id, task_id, sources)
    except ValidationError as e:
        return {"error": str(e)}


@mcp.tool()
async def research_wait_and_import(
    notebook_id: str,
    task_id: str,
    timeout_seconds: int = 300,
    interval_seconds: int = 5,
) -> dict[str, Any]:
    """Wait for research to finish then import all sources (CLI: ``research wait --import-all``). (Write)"""
    client = await get_client()
    deadline = time.monotonic() + max(1, timeout_seconds)
    interval = max(1, interval_seconds)
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = await client.research.poll(notebook_id)
        st = last.get("status")
        if st == "no_research":
            return {"error": "No research running", "status": "no_research"}
        if st in ("completed", "ready") and last.get("sources"):
            if last.get("task_id") != task_id:
                return {
                    "error": "Research completed for a different task_id",
                    "observed_task_id": last.get("task_id"),
                }
            try:
                imported = await client.research.import_sources(
                    notebook_id, task_id, last["sources"]
                )
            except ValidationError as e:
                return {"error": str(e), "status": st}
            return {
                "status": "imported",
                "task_id": task_id,
                "sources_imported": len(imported),
                "imported": imported,
            }
        await asyncio.sleep(interval)
    return {
        "error": "Timeout waiting for research",
        "status": "timeout",
        "last_poll": last,
    }


# --- Notebooks ---


@mcp.tool()
async def list_notebooks() -> list[dict[str, Any]]:
    """List all notebooks in your account. (ReadOnly)"""
    client = await get_client()
    notebooks = await client.notebooks.list()
    return [{"id": nb.id, "title": nb.title} for nb in notebooks]


@mcp.tool()
async def create_notebook(title: str) -> dict[str, Any]:
    """Create a new notebook. (Write)"""
    client = await get_client()
    nb = await client.notebooks.create(title)
    return {"id": nb.id, "title": nb.title}


@mcp.tool()
async def delete_notebook(notebook_id: str) -> bool:
    """Delete a notebook. (Destructive)"""
    client = await get_client()
    return await client.notebooks.delete(notebook_id)


@mcp.tool()
async def rename_notebook(notebook_id: str, new_title: str) -> dict[str, Any]:
    """Rename a notebook (CLI: ``notebook rename``). (Write)"""
    client = await get_client()
    nb = await client.notebooks.rename(notebook_id, new_title)
    return {"id": nb.id, "title": nb.title}


@mcp.tool()
async def get_notebook_summary(notebook_id: str) -> dict[str, Any]:
    """Get notebook summary and suggested topics. (ReadOnly)"""
    client = await get_client()
    description = await client.notebooks.get_description(notebook_id)
    return {
        "notebook_id": notebook_id,
        "summary": description.summary,
        "topics": [{"question": t.question} for t in description.suggested_topics],
    }


@mcp.tool()
async def get_share_status(notebook_id: str) -> dict[str, Any]:
    """Notebook sharing status and collaborator list (CLI: ``share status``). (ReadOnly)"""
    client = await get_client()
    status = await client.sharing.get_status(notebook_id)
    return _share_status_to_dict(status)


@mcp.tool()
async def set_notebook_public(notebook_id: str, public: bool) -> dict[str, Any]:
    """Enable or disable public link sharing (CLI: ``share public``). (Write)"""
    client = await get_client()
    status = await client.sharing.set_public(notebook_id, public)
    return _share_status_to_dict(status)


# --- Sources ---


@mcp.tool()
async def list_sources(notebook_id: str) -> list[dict[str, Any]]:
    """List all sources in a notebook. (ReadOnly)"""
    client = await get_client()
    sources = await client.sources.list(notebook_id)
    return [
        {"id": s.id, "title": s.title,
            "status": source_status_to_str(s.status), "url": s.url}
        for s in sources
    ]


@mcp.tool()
async def add_url_source(notebook_id: str, url: str, wait: bool = False) -> dict[str, Any]:
    """Add URL/YouTube source with deduplication. (Write)

    IMPORTANT: Always use ``wait=False`` (the default) when calling from ChatGPT/MCP.
    YouTube sources can take 2–5 minutes to index; using ``wait=True`` will block
    the SSE connection and cause a timeout/hang in the UI.

    Workflow for YouTube:
    1. Call ``add_url_source`` with ``wait=False`` → get source_id immediately.
    2. Call ``wait_source_ready`` with the returned source_id to poll until ready.
    """
    client = await get_client()
    existing = await find_existing_source(client, notebook_id, url)
    if existing:
        return {
            "id": existing.id,
            "status": source_status_to_str(existing.status),
            "note": "Duplicate blocked – source already exists.",
        }
    # Always add without blocking wait to prevent SSE timeout
    source = await client.sources.add_url(notebook_id, url, wait=False)
    result: dict[str, Any] = {
        "id": source.id,
        "title": source.title,
        "status": source_status_to_str(source.status),
    }
    if wait:
        # Cap at 55 s so we always return before the MCP/SSE connection times out
        try:
            ready = await asyncio.wait_for(
                client.sources.wait_until_ready(
                    notebook_id, source.id, timeout=55.0),
                timeout=57.0,
            )
            result["status"] = source_status_to_str(ready.status)
        except asyncio.TimeoutError:
            result["status"] = "processing"
            result["note"] = "Still indexing – call wait_source_ready to poll for completion."
        except Exception as e:
            result["status"] = "processing"
            result["note"] = f"Could not confirm ready: {e}. Call wait_source_ready to poll."
    else:
        result["note"] = "Source queued. Call wait_source_ready to check status before using."
    return result


@mcp.tool()
async def wait_source_ready(
    notebook_id: str,
    source_id: str,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Poll a source until it is ready (or timeout). Use after add_url_source. (ReadOnly)

    This is the correct way to wait for YouTube/URL sources without blocking the
    SSE connection. Call this in a loop or as a follow-up after ``add_url_source``.

    Returns:
        status: "ready" | "processing" | "error" | "timeout"
        is_ready: bool
    """
    client = await get_client()
    deadline = time.monotonic() + max(10, timeout_seconds)
    interval = 3.0
    last_status = "unknown"
    while time.monotonic() < deadline:
        source = await client.sources.get(notebook_id, source_id)
        if source is None:
            return {"status": "error", "is_ready": False, "note": "Source not found"}
        last_status = source_status_to_str(source.status)
        if source.is_ready:
            return {
                "source_id": source_id,
                "status": "ready",
                "is_ready": True,
                "title": source.title,
            }
        if source.is_error:
            return {"source_id": source_id, "status": "error", "is_ready": False}
        remaining = deadline - time.monotonic()
        await asyncio.sleep(min(interval, max(1.0, remaining)))
        interval = min(interval * 1.5, 15.0)
    return {
        "source_id": source_id,
        "status": "timeout",
        "is_ready": False,
        "last_status": last_status,
        "note": f"Still processing after {timeout_seconds}s. Try again later.",
    }


@mcp.tool()
async def add_text_source(notebook_id: str, title: str, text: str) -> dict[str, Any]:
    """Add text source. (Write)"""
    client = await get_client()
    if await find_existing_title(client, notebook_id, title, kind="source"):
        return {"error": f"Title '{title}' already exists."}
    source = await client.sources.add_text(notebook_id, title, text)
    return {"id": source.id, "title": source.title, "status": source_status_to_str(source.status)}


@mcp.tool()
async def refresh_source(notebook_id: str, source_id: str) -> bool:
    """Refresh an existing source. (Write)"""
    client = await get_client()
    return await client.sources.refresh(notebook_id, source_id)


@mcp.tool()
async def get_source_fulltext(notebook_id: str, source_id: str) -> dict[str, Any]:
    """Get the full indexed text of a source. (ReadOnly)"""
    client = await get_client()
    ft = await client.sources.get_fulltext(notebook_id, source_id)
    return {"content": ft.content, "char_count": ft.char_count}


@mcp.tool()
async def delete_source(notebook_id: str, source_id: str) -> bool:
    """Delete a source. (Destructive)"""
    client = await get_client()
    return await client.sources.delete(notebook_id, source_id)


@mcp.tool()
async def add_drive(
    notebook_id: str,
    file_id: str,
    title: str,
    mime_type: str = "application/vnd.google-apps.document",
) -> dict[str, Any]:
    """Add a Google Drive document as a source. (Write)

    Common mime_types:
    - application/vnd.google-apps.document (Google Docs)
    - application/vnd.google-apps.presentation (Slides)
    - application/pdf (PDF)
    """
    client = await get_client()
    source = await client.sources.add_drive(notebook_id, file_id, title, mime_type)
    return {"id": source.id, "title": source.title, "status": source_status_to_str(source.status)}


@mcp.tool()
async def add_file(
    notebook_id: str,
    file_path: str,
) -> dict[str, Any]:
    """Add a local file from the server's filesystem as a source. (Write)"""
    client = await get_client()
    source = await client.sources.add_file(notebook_id, file_path)
    return {"id": source.id, "title": source.title, "status": source_status_to_str(source.status)}


# --- Studio Content Generation ---


@mcp.tool()
async def generate_audio_overview(
    notebook_id: str,
    instructions: str | None = None,
    format: Literal["DEEP_DIVE", "BRIEF", "CRITIQUE", "DEBATE"] = "DEEP_DIVE",
    length: Literal["SHORT", "DEFAULT", "LONG"] = "DEFAULT",
) -> dict[str, Any]:
    """Generate Audio Overview (Podcast). (Write)"""
    client = await get_client()
    status = await client.artifacts.generate_audio(
        notebook_id,
        instructions=instructions,
        audio_format=AudioFormat[format],
        audio_length=AudioLength[length],
    )
    return {"task_id": status.task_id, "status": status.status}


@mcp.tool()
async def generate_video_overview(
    notebook_id: str,
    instructions: str | None = None,
    format: Literal["EXPLAINER", "BRIEF"] = "EXPLAINER",
    style: Literal[
        "AUTO_SELECT", "CLASSIC", "WHITEBOARD", "PLAYFUL", "PROFESSIONAL"
    ] = "AUTO_SELECT",
) -> dict[str, Any]:
    """Generate Video Overview. (Write)"""
    client = await get_client()
    status = await client.artifacts.generate_video(
        notebook_id,
        instructions=instructions,
        video_format=VideoFormat[format],
        video_style=VideoStyle[style],
    )
    return {"task_id": status.task_id, "status": status.status}


@mcp.tool()
async def generate_cinematic_video(
    notebook_id: str, instructions: str | None = None
) -> dict[str, Any]:
    """Generate a Cinematic Video. (Write)"""
    client = await get_client()
    status = await client.artifacts.generate_cinematic_video(notebook_id, instructions=instructions)
    return {"task_id": status.task_id, "status": status.status}


@mcp.tool()
async def generate_report(
    notebook_id: str,
    format: Literal["BRIEFING_DOC", "STUDY_GUIDE",
                    "BLOG_POST", "CUSTOM"] = "BRIEFING_DOC",
    extra_instructions: str | None = None,
) -> dict[str, Any]:
    """Generate a specialized report. (Write)"""
    client = await get_client()
    status = await client.artifacts.generate_report(
        notebook_id, report_format=ReportFormat[format], extra_instructions=extra_instructions
    )
    return {"task_id": status.task_id, "status": status.status}


@mcp.tool()
async def generate_quiz(
    notebook_id: str,
    difficulty: Literal["EASY", "MEDIUM", "HARD"] = "MEDIUM",
    quantity: Literal["FEWER", "STANDARD", "MORE"] = "STANDARD",
) -> dict[str, Any]:
    """Generate a quiz. (Write)"""
    client = await get_client()
    status = await client.artifacts.generate_quiz(
        notebook_id, difficulty=QuizDifficulty[difficulty], quantity=QuizQuantity[quantity]
    )
    return {"task_id": status.task_id, "status": status.status}


@mcp.tool()
async def generate_flashcards(
    notebook_id: str,
    difficulty: Literal["EASY", "MEDIUM", "HARD"] = "MEDIUM",
    quantity: Literal["FEWER", "STANDARD", "MORE"] = "STANDARD",
) -> dict[str, Any]:
    """Generate flashcards. (Write)"""
    client = await get_client()
    status = await client.artifacts.generate_flashcards(
        notebook_id, difficulty=QuizDifficulty[difficulty], quantity=QuizQuantity[quantity]
    )
    return {"task_id": status.task_id, "status": status.status}


@mcp.tool()
async def generate_infographic(
    notebook_id: str,
    orientation: Literal["LANDSCAPE", "PORTRAIT", "SQUARE"] = "PORTRAIT",
    detail: Literal["CONCISE", "STANDARD", "DETAILED"] = "STANDARD",
    style: Literal["AUTO_SELECT", "CLASSIC", "MODERN"] = "AUTO_SELECT",
) -> dict[str, Any]:
    """Generate an infographic. (Write)"""
    client = await get_client()
    status = await client.artifacts.generate_infographic(
        notebook_id,
        orientation=InfographicOrientation[orientation],
        detail_level=InfographicDetail[detail],
        style=InfographicStyle[style],
    )
    return {"task_id": status.task_id, "status": status.status}


@mcp.tool()
async def generate_slide_deck(
    notebook_id: str,
    format: Literal["DETAILED_DECK", "PRESENTER_SLIDES"] = "DETAILED_DECK",
    length: Literal["DEFAULT", "SHORT"] = "DEFAULT",
) -> dict[str, Any]:
    """Generate presentation slides. (Write)"""
    client = await get_client()
    status = await client.artifacts.generate_slide_deck(
        notebook_id, slide_format=SlideDeckFormat[format], slide_length=SlideDeckLength[length]
    )
    return {"task_id": status.task_id, "status": status.status}


@mcp.tool()
async def generate_data_table(notebook_id: str, instructions: str) -> dict[str, Any]:
    """Generate a structured data table. (Write)"""
    client = await get_client()
    status = await client.artifacts.generate_data_table(notebook_id, instructions=instructions)
    return {"task_id": status.task_id, "status": status.status}


@mcp.tool()
async def generate_mind_map(notebook_id: str) -> dict[str, Any]:
    """Generate interactive Mind Map saved as a note. (Write)"""
    client = await get_client()
    return await client.artifacts.generate_mind_map(notebook_id)


@mcp.tool()
async def poll_artifact_status(notebook_id: str, task_id: str) -> dict[str, Any]:
    """Check task completion. (ReadOnly)"""
    client = await get_client()
    status = await client.artifacts.poll_status(notebook_id, task_id)
    return {"status": status.status, "is_complete": status.is_complete}


@mcp.tool()
async def list_artifacts(notebook_id: str) -> list[dict[str, Any]]:
    """List AI-generated artifacts in the notebook (CLI: ``artifact list``). (ReadOnly)"""
    client = await get_client()
    artifacts = await client.artifacts.list(notebook_id)
    return [_artifact_to_dict(a) for a in artifacts]


@mcp.tool()
async def get_artifact(notebook_id: str, artifact_id: str) -> dict[str, Any]:
    """Get one artifact by id (metadata; CLI: ``artifact get``). (ReadOnly)"""
    client = await get_client()
    artifact = await client.artifacts.get(notebook_id, artifact_id)
    if artifact is None:
        return {"error": "Artifact not found"}
    return _artifact_to_dict(artifact)


# --- Notes & Chat ---


@mcp.tool()
async def ask_question(notebook_id: str, question: str) -> str:
    """Ask sources via NotebookLM RAG engine. (ReadOnly)"""
    client = await get_client()
    result = await client.chat.ask(notebook_id, question)
    return getattr(result, "answer", str(result))


@mcp.tool()
async def list_notes(notebook_id: str) -> list[dict[str, Any]]:
    """List text notes. (ReadOnly)"""
    client = await get_client()
    notes = await client.notes.list(notebook_id)
    return [{"id": n.id, "title": n.title} for n in notes]


@mcp.tool()
async def create_note(notebook_id: str, title: str, content: str) -> dict[str, Any]:
    """Create a new note. Check for duplicates first. (Write)"""
    client = await get_client()
    if await find_existing_title(client, notebook_id, title, kind="note"):
        return {"error": f"Note '{title}' already exists."}
    note = await client.notes.create(notebook_id, title, content)
    return {"id": note.id, "title": note.title}


@mcp.tool()
async def get_note(notebook_id: str, note_id: str) -> dict[str, Any]:
    """Read a note's title and body (CLI: ``note get``). (ReadOnly)"""
    client = await get_client()
    note = await client.notes.get(notebook_id, note_id)
    if note is None:
        return {"error": "Note not found"}
    return {"id": note.id, "title": note.title, "content": note.content}


@mcp.tool()
async def rename_note(notebook_id: str, note_id: str, new_title: str) -> dict[str, Any]:
    """Rename a note (CLI: ``note rename``). (Write)"""
    client = await get_client()
    note = await client.notes.get(notebook_id, note_id)
    if note is None:
        return {"error": "Note not found"}
    await client.notes.update(notebook_id, note_id, note.content, new_title)
    return {"id": note_id, "title": new_title}


@mcp.tool()
async def delete_note(notebook_id: str, note_id: str) -> bool:
    """Delete a note (CLI: ``note delete``). (Destructive)"""
    client = await get_client()
    return await client.notes.delete(notebook_id, note_id)


@mcp.tool()
async def export_artifact(
    notebook_id: str, artifact_id: str, type: Literal["DOCS", "SHEETS"] = "DOCS"
) -> Any:
    """Export Report/Table to Google Docs/Sheets. (Write)"""
    client = await get_client()
    return await client.artifacts.export(
        notebook_id, artifact_id=artifact_id, export_type=ExportType[type]
    )


# --- Build transport sub-apps eagerly so we can manage their lifecycles ---

# Streamable HTTP transport (Claude AI)
_streamable_app = mcp.streamable_http_app()

# Legacy SSE transport (ChatGPT backward compatibility)
_sse_app = mcp.sse_app()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Manage the lifecycle of all MCP transports and the NotebookLM client."""
    # Start the Streamable HTTP session manager task group.
    if mcp._session_manager:
        async with mcp._session_manager.run():
            logger.info("✅ Streamable HTTP session manager started")
            yield
    else:
        logger.warning(
            "⚠️ Streamable HTTP session manager is None, skipping run()")
        yield

    # Shutdown: clean up the NotebookLM client
    global _client
    if _client:
        await _client.__aexit__(None, None, None)


app = FastAPI(title="NotebookLM Framework MCP", lifespan=lifespan)


# --- Ultra-Stable Proxy Handlers ---


@app.get("/health")
async def health():
    """Health check endpoint reporting available MCP transports."""
    return {
        "status": "ok",
        "transports": {
            "streamable_http": "/mcp",
            "sse_legacy": "/sse",
        },
        "clients": {
            "claude_ai": "https://notebooklm-mcp.tawanlab.site/mcp",
            "chatgpt": "https://notebooklm-mcp.tawanlab.site/sse/",
        },
    }


# Proxy for SSE transport with Cloudflare-friendly headers
class SSEProxy:
    def __init__(self, app_to_proxy):
        self.app = app_to_proxy

    async def __call__(self, scope, receive, send):
        """Proxy for SSE transport with Cloudflare-friendly headers."""
        scope["root_path"] = ""
        scope["path"] = "/sse"

        # Add headers via a custom send wrapper
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                headers[b"cache-control"] = b"no-cache, no-transform"
                headers[b"x-accel-buffering"] = b"no"
                headers[b"connection"] = b"keep-alive"
                if b"transfer-encoding" in headers:
                    del headers[b"transfer-encoding"]
                message["headers"] = list(headers.items())
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except ClosedResourceError:
            pass


class MCPProxy:
    def __init__(self, app_to_proxy):
        self.app = app_to_proxy

    async def __call__(self, scope, receive, send):
        """Proxy for Streamable HTTP transport (Claude) with prefix restoration."""
        scope["root_path"] = ""
        scope["path"] = "/"

        try:
            await self.app(scope, receive, send)
        except ClosedResourceError:
            pass


# Add routes explicitly to support both with and without trailing slashes
app.add_route("/sse", SSEProxy(_sse_app), methods=["GET", "OPTIONS"])  # type: ignore[arg-type]
app.add_route("/sse/", SSEProxy(_sse_app), methods=["GET", "OPTIONS"])  # type: ignore[arg-type]

app.add_route("/messages", _sse_app, methods=["POST", "OPTIONS"])  # type: ignore[arg-type]
app.add_route("/messages/{path:path}", _sse_app, methods=["POST", "OPTIONS"])  # type: ignore[arg-type]

app.add_route("/mcp", MCPProxy(_streamable_app), methods=["GET", "POST", "OPTIONS"])  # type: ignore[arg-type]
app.add_route("/mcp/{path:path}", MCPProxy(_streamable_app), methods=["GET", "POST", "OPTIONS"])  # type: ignore[arg-type]


def main():
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 8000)),
        proxy_headers=True,
        forwarded_allow_ips="*",
        timeout_keep_alive=75,
        timeout_graceful_shutdown=30,
        # Allow large SSE frames from long-running tool responses
        h11_max_incomplete_event_size=16 * 1024 * 1024,
    )


if __name__ == "__main__":
    main()
