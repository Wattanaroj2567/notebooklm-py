import asyncio
import base64
import csv
import datetime
import io
import json
import logging
import os
import re
import tempfile
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

import uvicorn
from anyio import ClosedResourceError
from fastapi import FastAPI, Request
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
_TOLERANT_DECODER_FILTER_MARKER = "_notebooklm_mcp_tolerant_decoder_filter"


class _TolerantDecoderWarningFilter(logging.Filter):
    """Suppress noisy decoder warnings that are known to be recoverable in MCP logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "notebooklm.rpc.decoder":
            return True
        message = record.getMessage()
        return not (
            message.startswith("Chunk at line ") and "parsing valid JSON payload anyway" in message
        )


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def _configure_mcp_logging() -> None:
    logger.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    decoder_logger = logging.getLogger("notebooklm.rpc.decoder")
    decoder_logger.filters = [
        f for f in decoder_logger.filters if not getattr(f, _TOLERANT_DECODER_FILTER_MARKER, False)
    ]
    if not _env_truthy("NOTEBOOKLM_MCP_SHOW_TOLERANT_DECODER_WARNINGS"):
        noise_filter = _TolerantDecoderWarningFilter()
        setattr(noise_filter, _TOLERANT_DECODER_FILTER_MARKER, True)
        decoder_logger.addFilter(noise_filter)


logger = logging.getLogger("notebooklm-mcp")
_configure_mcp_logging()

# --- NotebookLM AI Framework Configuration ---
FRAMEWORK_NOTEBOOK_ID_ENV = "NOTEBOOKLM_FRAMEWORK_NOTEBOOK_ID"
FRAMEWORK_ROLES = ("auto", "Minnie", "Reas", "Vera", "Indy", "Day", "Chris")
NOTEBOOKLM_BASE_URL = "https://notebooklm.google.com"


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


def notebook_url(notebook_id: str | None) -> str | None:
    if not notebook_id:
        return None
    return f"{NOTEBOOKLM_BASE_URL}/notebook/{notebook_id}"


def with_notebook_url(result: dict[str, Any], notebook_id: str | None) -> dict[str, Any]:
    if notebook_id:
        result.setdefault("notebook_id", notebook_id)
        result["notebook_url"] = notebook_url(notebook_id)
    # Inject auth notice so any AI reading the response can surface expiry
    # warnings to the user without them having to check Docker logs.
    notice = _get_auth_notice()
    if notice is not None:
        result["_auth_notice"] = notice
    return result


def _artifact_content_format_hint(kind: str) -> str:
    if kind == "data_table":
        return "json"
    if kind in {"report", "quiz", "flashcards", "mind_map"}:
        return "markdown"
    return "plain_text"


def _artifact_supports_content_retrieval(kind: str) -> bool:
    return kind in {"report", "quiz", "flashcards", "data_table", "mind_map"}


def _status_to_generation_response(
    notebook_id: str,
    status: Any,
    next_tool: str = "poll_artifact_status",
) -> dict[str, Any]:
    task_id = getattr(status, "task_id", None)
    result = {
        "task_id": task_id,
        "status": getattr(status, "status", None),
        "is_complete": getattr(status, "is_complete", False),
        "notebook_id": notebook_id,
        "notebook_url": notebook_url(notebook_id),
    }
    if getattr(status, "url", None):
        result["url"] = status.url
    if getattr(status, "error", None):
        result["error"] = status.error
    if task_id:
        result["next_action"] = {
            "tool": next_tool,
            "args": {"notebook_id": notebook_id, "task_id": task_id},
        }
    return result


def _parse_csv_table(content: str) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(content))
    rows = [dict(row) for row in reader]
    columns = list(reader.fieldnames or [])
    return {"columns": columns, "rows": rows}


def _csv_to_markdown(content: str) -> str:
    parsed = _parse_csv_table(content)
    columns = parsed["columns"]
    rows = parsed["rows"]
    if not columns:
        return content
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        values = [str(row.get(column, "")).replace("\n", " ") for column in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _coerce_artifact_content(content: str, kind: str, requested_format: str) -> dict[str, Any]:
    content_format = requested_format
    coerced: Any = content
    extra: dict[str, Any] = {}

    if requested_format == "auto":
        content_format = "csv" if kind == "data_table" else _artifact_content_format_hint(kind)

    if kind == "data_table" and content_format == "json":
        table = _parse_csv_table(content)
        coerced = table
        extra = table
    elif kind == "data_table" and content_format == "markdown":
        coerced = _csv_to_markdown(content)
    elif content_format == "json":
        try:
            coerced = json.loads(content)
        except json.JSONDecodeError:
            content_format = "plain_text"
            coerced = content
    elif content_format == "plain_text":
        coerced = content

    return {"content_format": content_format, "content": coerced, **extra}


def _source_to_delete_info(source: Any) -> dict[str, Any]:
    return {
        "source_id": getattr(source, "id", None),
        "title": getattr(source, "title", None),
        "url": getattr(source, "url", None),
    }


def _note_to_delete_info(note: Any) -> dict[str, Any]:
    return {"note_id": getattr(note, "id", None), "title": getattr(note, "title", None)}


def _share_status_to_dict(status: ShareStatus) -> dict[str, Any]:
    users: list[dict[str, Any]] = []
    for u in status.shared_users:
        perm = u.permission.name if hasattr(u.permission, "name") else str(u.permission)
        users.append(
            {
                "email": u.email,
                "permission": perm,
                "display_name": u.display_name,
            }
        )
    access = status.access.name if hasattr(status.access, "name") else str(status.access)
    view = status.view_level.name if hasattr(status.view_level, "name") else str(status.view_level)
    return with_notebook_url(
        {
            "notebook_id": status.notebook_id,
            "is_public": status.is_public,
            "access": access,
            "view_level": view,
            "share_url": status.share_url,
            "shared_users": users,
        },
        status.notebook_id,
    )


# Load advanced instructions from MCP_SKILL.md if available
default_instructions = """You are the Orchestrator (Indy) of the NotebookLM AI Agent Framework.
CRITICAL MCP TOOL USAGE RULES:
1. **FRAMEWORK KNOWLEDGE**: When you need role playbooks, AI-team behavior, reusable SOPs, or "how should this agent work?", call `ask_framework_manual`. It reads the configured NotebookLM framework notebook via `NOTEBOOKLM_FRAMEWORK_NOTEBOOK_ID`. Do not rely on removed local framework files for new workflows.
2. **ROLE ROUTING**: Use `role` in `ask_framework_manual` for specialist context: Minnie=planning/intake, Reas=reasoning/synthesis, Vera=verification/source hygiene, Indy=orchestration/research, Day=writing/polish, Chris=technical/build.
3. **DEEP RESEARCH (new notebook)**: Use `run_deep_search_workflow`; then follow its structured `next_action` (`research_wait_and_import`).
4. **RESEARCH (existing notebook)**: Use `start_research`, then `poll_research_results` or `research_wait_and_import`, then `import_research_sources` only when importing a subset manually.
5. **SINGLE-URL INGEST**: Use `run_research_team_workflow` when the user gives one primary URL/YouTube link and wants a notebook plus optional Thai summary.
6. **DEDUPLICATION**: Prefer `get_or_create_notebook`; source and note tools block obvious duplicates, but still reuse known IDs when available.
7. **LONG-RUNNING ARTIFACTS**: After any generation tool, follow the returned `next_action`. Poll with `poll_artifact_status`; when completed, use its `next_action` to call `get_artifact_content`.
8. **ARTIFACT CONTENT**: Request `format="json"` for data tables when downstream automation needs rows/columns. Use `content_format` instead of guessing.
9. **DESTRUCTIVE TOOLS**: Always dry-run first. Only call delete tools with `dry_run=False` and `confirm=True` after the user explicitly approves the dry-run target list.
10. **THAI FIRST**: Summarize research in Thai unless the user asks for another language.
11. **STRICT JSON**: For machine-readable RAG, use `ask_question` or `ask_framework_manual` with `response_format="json"` and `strict_json=True`; handle `ok=false` as a structured failure."""

try:
    skill_path = Path(__file__).parent.parent.parent / "MCP_SKILL.md"
    if not skill_path.exists():
        skill_path = Path("/app/MCP_SKILL.md")

    if skill_path.exists():
        skill_content = skill_path.read_text(encoding="utf-8")
        default_instructions = (
            f"{skill_content}\n\n=== ADDITIONAL CONTEXT ===\n{default_instructions}"
        )
        logger.info("✅ Successfully loaded advanced AI instructions from MCP_SKILL.md")
except Exception as e:
    logger.warning(f"⚠️ Could not load MCP_SKILL.md. Error: {e}")

mcp = FastMCP(
    "NotebookLM-Agent-Framework",
    instructions=default_instructions,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
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
    logger.error(f"❌ Failed to load custom MCP icon. Error: {e}", exc_info=True)
# Global client state
_client: NotebookLMClient | None = None
_client_lock = asyncio.Lock()

# --- Auth Health Monitoring ---

# Cookies we care about for session lifetime (not rotation cookies like PSIDTS —
# those are handled by the systemd keepalive timer).
_WATCHED_COOKIES = frozenset(
    {
        "SID",
        "__Secure-1PSID",
        "__Secure-3PSID",
        "SIDCC",
        "__Secure-1PSIDCC",
        "OSID",
    }
)

# Warn when any watched cookie expires within this many days.
_AUTH_WARN_DAYS = 7

# How long (seconds) to cache the auth-notice result to avoid per-call disk reads.
_AUTH_NOTICE_TTL = 300  # 5 minutes
# Cache entry: (notice_dict | None, monotonic_expiry)
_auth_notice_cache: tuple[dict[str, Any] | None, float] = (None, 0.0)

_CONTAINER_KEEPALIVE_SECONDS = 300


def _is_hard_auth_failure(exc: BaseException) -> bool:
    """Return True when Google rejected the stored cookies, not just a stale token."""
    message = str(exc).lower()
    return (
        "authentication expired or invalid" in message
        or "accounts.google.com/v3/signin" in message
        or "signin/accountchooser" in message
        or "run 'notebooklm login'" in message
    )


def _live_auth_failure_message(exc: BaseException) -> str:
    if _is_hard_auth_failure(exc):
        return (
            "Cookie file exists, but Google rejected it during a live auth check. "
            "This is not healthy auth. Do not click Google redirect URLs from logs; "
            "they cannot update the MCP storage_state.json. Run `notebooklm login --fresh` "
            "or `notebooklm login --browser-cookies chrome`, verify with "
            "`notebooklm auth check --test`, then restart the MCP container."
        )
    return (
        "Cookie file exists, but a live NotebookLM RPC failed. Restart the MCP "
        "container; if it still fails, run `notebooklm auth check --test` and "
        "re-authenticate."
    )


def _check_auth_health() -> dict[str, Any]:
    """Inspect storage_state.json and return auth health summary.

    Returns a dict with keys:
      status        : "ok" | "warn" | "expired" | "unknown"
      days_until_expiry : float | None   (None = no expiry info found)
      soonest_expiry    : ISO-8601 str | None
      expired_cookies   : list[str]      (names of already-expired cookies)
      warn_cookies      : list[str]      (names expiring within _AUTH_WARN_DAYS)
      fix_command       : str | None     (command the user should run)
      message           : str            (human-readable one-liner for logs)
    """
    from notebooklm.paths import get_storage_path

    result: dict[str, Any] = {
        "status": "ok",
        "days_until_expiry": None,
        "soonest_expiry": None,
        "expired_cookies": [],
        "warn_cookies": [],
        "fix_command": None,
        "message": "Auth cookies look healthy.",
    }

    # Support inline JSON auth (env-var path) — we can't inspect expiry there.
    if os.environ.get("NOTEBOOKLM_AUTH_JSON"):
        result["message"] = "Auth via NOTEBOOKLM_AUTH_JSON — expiry check skipped."
        return result

    storage_path = get_storage_path()
    if not storage_path.exists():
        result["status"] = "unknown"
        result["fix_command"] = "notebooklm login"
        result["message"] = f"storage_state.json not found at {storage_path}. Run: notebooklm login"
        return result

    try:
        data = json.loads(storage_path.read_text(encoding="utf-8"))
    except Exception as exc:
        result["status"] = "unknown"
        result["fix_command"] = "notebooklm login"
        result["message"] = f"Cannot read storage_state.json: {exc}"
        return result

    now = time.time()
    min_exp: float | None = None
    expired: list[str] = []
    warn: list[str] = []

    for cookie in data.get("cookies", []):
        name = cookie.get("name", "")
        if name not in _WATCHED_COOKIES:
            continue
        exp = cookie.get("expires", -1)
        if exp < 0:  # session cookie — no explicit expiry timestamp
            continue
        if exp < now:
            expired.append(name)
        else:
            days_left = (exp - now) / 86400
            if days_left < _AUTH_WARN_DAYS:
                warn.append(name)
            if min_exp is None or exp < min_exp:
                min_exp = exp

    result["expired_cookies"] = expired
    result["warn_cookies"] = warn

    if min_exp is not None:
        days_left = (min_exp - now) / 86400
        result["days_until_expiry"] = round(days_left, 1)
        result["soonest_expiry"] = datetime.datetime.fromtimestamp(
            min_exp, tz=datetime.timezone.utc
        ).isoformat()

    if expired:
        result["status"] = "expired"
        result["fix_command"] = "notebooklm login"
        result["message"] = (
            f"❌ AUTH EXPIRED — cookies: {', '.join(expired)}. Run: notebooklm login"
        )
    elif warn:
        result["status"] = "warn"
        result["fix_command"] = "notebooklm login"
        result["message"] = (
            f"⚠️  AUTH EXPIRING SOON — {result['days_until_expiry']} days left "
            f"(cookies: {', '.join(warn)}). Run: notebooklm login"
        )
    elif min_exp is not None:
        result["message"] = (
            f"✅ Auth cookies OK — soonest expiry in {result['days_until_expiry']} days "
            f"({result['soonest_expiry']})."
        )

    return result


def _get_auth_notice() -> dict[str, Any] | None:
    """Return a compact auth warning dict, or None when auth is healthy.

    Result is cached for _AUTH_NOTICE_TTL seconds so the per-call overhead
    is a single monotonic clock read in the happy path.

    Returned dict (when not None) has keys:
      status      : "warn" | "expired" | "unknown"
      message     : human-readable one-liner
      fix_command : CLI command to restore auth
    """
    global _auth_notice_cache
    cached_notice, cache_expires_at = _auth_notice_cache
    now_mono = time.monotonic()
    if now_mono < cache_expires_at:
        return cached_notice  # fast path: serve from cache

    health = _check_auth_health()
    notice: dict[str, Any] | None
    if health["status"] == "ok":
        notice = None
    else:
        notice = {
            "status": health["status"],
            "message": health["message"],
            "fix_command": health["fix_command"],
        }
    _auth_notice_cache = (notice, now_mono + _AUTH_NOTICE_TTL)
    return notice


def _invalidate_auth_notice_cache() -> None:
    """Force the next _get_auth_notice() call to re-read storage_state.json."""
    global _auth_notice_cache
    _auth_notice_cache = (None, 0.0)


async def _auth_watcher(interval_seconds: int = 3600) -> None:
    """Background task: log auth health every *interval_seconds* (default 1 h).

    Logs at WARNING when status is 'warn', ERROR when 'expired', INFO otherwise.
    Designed to be fire-and-forget from lifespan.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            health_info = _check_auth_health()
            status = health_info["status"]
            msg = health_info["message"]
            if status == "expired":
                logger.error("[auth-watcher] %s", msg)
            elif status == "warn":
                logger.warning("[auth-watcher] %s", msg)
            else:
                logger.info("[auth-watcher] %s", msg)
        except Exception as exc:
            logger.warning("[auth-watcher] health check failed: %s", exc)


async def get_client() -> NotebookLMClient:
    global _client
    async with _client_lock:
        if _client is None:
            try:
                client = await NotebookLMClient.from_storage(
                    timeout=120.0, keepalive=_CONTAINER_KEEPALIVE_SECONDS
                )
                await client.__aenter__()
                _client = client
                logger.info("NotebookLM client initialized successfully")
            except Exception as e:
                # Never leave a half-initialized client cached: if __aenter__
                # failed, _client must stay None so the next call retries.
                _client = None
                logger.error("Failed to initialize client: %s", _live_auth_failure_message(e))
                # Force next _get_auth_notice() to re-read disk so callers
                # immediately see the expired/unknown notice in their response.
                _invalidate_auth_notice_cache()
                auth_info = _check_auth_health()
                if auth_info["status"] in ("expired", "unknown"):
                    logger.error("[auth] %s", auth_info["message"])
                    raise RuntimeError(
                        f"NotebookLM client not ready. {auth_info['message']}"
                    ) from e
                raise RuntimeError(
                    f"NotebookLM client not ready. {_live_auth_failure_message(e)}"
                ) from e
        return _client


async def _session_refresh_task(interval_seconds: int = 600) -> None:
    """Background task: re-fetch the NotebookLM homepage every *interval_seconds* (default 10 min).

    Google's FdrFJe (f.sid) and SNlM0e (csrf_token) are server-side session tokens
    extracted once at startup. They become stale after a few hours, causing batchexecute
    to return HTTP 200 with a gRPC code-16 (Unauthenticated) payload.

    The core layer now auto-detects code 16 (is_auth_error → refresh+retry), so a
    stale token self-heals on the next call. This periodic refresh is
    defense-in-depth that keeps idle sessions warm so the first call after a
    quiet period doesn't pay the refresh+retry round-trip.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            client = await get_client()
            await client.refresh_auth()
            logger.info("[session-refresh] ✅ Session tokens (csrf_token, session_id) refreshed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[session-refresh] ⚠️ Failed to refresh session tokens: %s", exc)


# --- Framework Tools ---


@mcp.tool()
async def ask_framework_manual(
    question: str,
    role: Literal["auto", "Minnie", "Reas", "Vera", "Indy", "Day", "Chris"] = "auto",
    response_format: Literal["text", "json"] = "text",
    strict_json: bool = False,
    citations_mode: Literal["inline", "separate"] = "separate",
) -> dict[str, Any]:
    """Ask the configured Framework Notebook for role playbooks and MCP workflow guidance. (ReadOnly)"""
    framework_notebook_id = os.getenv(FRAMEWORK_NOTEBOOK_ID_ENV)
    if not framework_notebook_id:
        return {
            "ok": False,
            "error": {
                "code": "FRAMEWORK_NOTEBOOK_NOT_CONFIGURED",
                "message": (
                    f"Set {FRAMEWORK_NOTEBOOK_ID_ENV} to the NotebookLM notebook ID that "
                    "contains the AI-team manuals, role playbooks, and MCP workflow docs."
                ),
            },
            "next_action": {
                "tool": "get_or_create_notebook",
                "args": {
                    "title": "NotebookLM MCP Framework Manual",
                    "reuse_existing": True,
                },
            },
        }

    role_context = "" if role == "auto" else f"Answer from the {role} role perspective. "
    final_question = (
        f"{role_context}"
        "Use the Framework Notebook as the source of truth for NotebookLM MCP usage, "
        "AI-team roles, and workflow ergonomics. "
        f"Question: {question}"
    )
    if response_format == "json":
        final_question += (
            "\n\nReply in strictly valid JSON format only, without markdown code fences."
        )
        if strict_json:
            final_question += " Do not include citation markers like [1] or [2] inside JSON values."

    try:
        client = await get_client()
        result = await client.chat.ask(framework_notebook_id, final_question)
    except RuntimeError as e:
        return {
            "ok": False,
            "notebook_id": framework_notebook_id,
            "notebook_url": notebook_url(framework_notebook_id),
            "role": role,
            "error": {
                "code": "NOTEBOOKLM_AUTH_NOT_READY",
                "message": (
                    "NotebookLM authentication is not ready in the MCP server. "
                    "Run `notebooklm auth check --test`; if token fetch fails, "
                    "run `notebooklm login` or refresh browser cookies, then restart the MCP container."
                ),
            },
            "detail": str(e),
        }
    answer_text = getattr(result, "answer", str(result))

    output: dict[str, Any] = {
        "ok": True,
        "notebook_id": framework_notebook_id,
        "notebook_url": notebook_url(framework_notebook_id),
        "role": role,
        "answer": answer_text,
    }

    if response_format == "json" and strict_json:
        cleaned = re.sub(r"^```json\s*", "", answer_text, flags=re.IGNORECASE)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return {
                "ok": False,
                "notebook_id": framework_notebook_id,
                "notebook_url": notebook_url(framework_notebook_id),
                "role": role,
                "error": {
                    "code": "STRICT_JSON_PARSE_FAILED",
                    "message": "The framework answer could not be parsed as valid JSON.",
                },
                "raw_answer": answer_text,
                "repair_attempted": False,
                "repair_success": False,
            }
        output["answer"] = parsed
        output["result"] = {"answer": parsed}
        output["repair_attempted"] = False
        output["repair_success"] = False

    if citations_mode == "separate":
        refs = getattr(result, "references", [])
        output["citations"] = [
            {
                "source_id": r.source_id,
                "citation_number": r.citation_number,
                "quote": r.cited_text,
            }
            for r in refs
        ]

    notice = _get_auth_notice()
    if notice is not None:
        output["_auth_notice"] = notice

    return output


@mcp.tool()
async def check_auth_status(deep: bool = True) -> dict[str, Any]:
    """Check NotebookLM authentication health and report expiry status. (ReadOnly)

    Call this tool when the user asks about authentication status, session
    health, or cookie expiry — or proactively before starting a long workflow
    to avoid mid-task failures.

    ``deep`` (default True) additionally performs a live ``list_notebooks``
    RPC so the result reflects the real session token (f.sid / csrf_token),
    not just cookie expiry in storage_state.json. Cookies can look healthy
    for a year while the session token is already stale — the cookie-only
    check would wrongly report "ok". Pass deep=False for a fast offline read.

    Returns:
      status        : "ok" | "warn" | "expired" | "unknown"
      message       : human-readable summary (safe to show directly to the user)
      fix_command   : CLI command to restore auth, or null when status is ok
      days_until_expiry : days until the soonest session cookie expires, or null
      soonest_expiry    : ISO-8601 timestamp of that cookie, or null
      expired_cookies   : list of already-expired cookie names
      warn_cookies      : list of cookies expiring within 7 days
      cookie_rotator    : which process should rotate short-lived cookies
    """
    # Always bypass cache here so the user gets a live reading on demand.
    _invalidate_auth_notice_cache()
    health = _check_auth_health()

    response: dict[str, Any] = {
        **health,
        "file_check": health["status"],
        "cookie_rotator": {
            "active": "mcp_container",
            "container_keepalive_seconds": _CONTAINER_KEEPALIVE_SECONDS,
            "host_systemd_timer": "disabled",
            "message": (
                "Use one cookie rotator only. This deployment expects the MCP "
                "container keepalive to rotate cookies; keep "
                "`notebooklm-keepalive.timer` disabled on the host."
            ),
        },
    }

    if health["status"] == "ok":
        response["advice"] = "Auth is healthy. No action needed."
    elif health["status"] == "warn":
        response["advice"] = (
            f"Session cookies expire in {health['days_until_expiry']} days. "
            "Re-login soon to avoid interruption: notebooklm login"
        )
    elif health["status"] == "expired":
        response["advice"] = (
            "Session has expired. Run the fix command, then restart the MCP container: "
            "docker compose restart notebooklm-mcp"
        )
    else:
        response["advice"] = "Auth state is unknown. Run the fix command to initialise a session."

    if deep:
        live: dict[str, Any] = {"checked": True}
        try:
            client = await get_client()
            notebooks = await client.notebooks.list()
            live["ok"] = True
            live["notebook_count"] = len(notebooks)
            live["message"] = "Live list_notebooks RPC succeeded — session token is valid."
        except Exception as exc:
            live["ok"] = False
            live["error"] = str(exc)
            live["message"] = _live_auth_failure_message(exc)
            # The live probe is authoritative: a stale session token is the
            # real failure even when cookies are fine on disk.
            response["status"] = "expired" if health["status"] == "ok" else health["status"]
            response["fix_command"] = "notebooklm login"
            response["advice"] = live["message"]
        response["live_probe"] = live

    return response


def _source_readiness_summary(sources: list[Any]) -> dict[str, Any]:
    ready = 0
    processing = 0
    errors = 0
    for source in sources:
        raw_status = getattr(source, "status", None)
        status = source_status_to_str(raw_status) if raw_status is not None else "unknown"
        if status == "ready":
            ready += 1
        elif status in {"processing", "new", "pending"}:
            processing += 1
        elif status == "error":
            errors += 1
    return {
        "count": len(sources),
        "ready_count": ready,
        "processing_count": processing,
        "error_count": errors,
    }


def _file_ingestion_capability() -> dict[str, Any]:
    root_env = os.environ.get(FILE_ROOT_ENV)
    diagnostics = _file_root_diagnostics("<file_path>", root_env)
    if not root_env:
        return {
            "ok": False,
            "code": "FILE_ROOT_NOT_CONFIGURED",
            "diagnostics": diagnostics,
            "message": f"Set {FILE_ROOT_ENV} or use add_text_source.",
        }
    root = Path(root_env)
    return {
        "ok": root.exists() and root.is_dir(),
        "code": "OK" if root.exists() and root.is_dir() else "FILE_ROOT_NOT_FOUND",
        "diagnostics": diagnostics,
        "message": (
            "Copy files into ./mcp_imports and call add_file with /imports/<filename>."
            if str(root_env) == DEFAULT_DOCKER_FILE_ROOT
            else f"Use files under {root.resolve()} for add_file."
        ),
    }


@mcp.tool()
async def check_mcp_readiness(notebook_id: str | None = None) -> dict[str, Any]:
    """Summarize MCP readiness for auth, read, text write, and file ingestion. (ReadOnly)

    This is the first tool an agent should call before a multi-step workflow.
    It is non-destructive: it does not create notebooks, add sources, or clean
    anything up. ``ready_for_text_write`` means the live NotebookLM client is
    authenticated and write tools should be usable; it is not a write probe.
    """
    _invalidate_auth_notice_cache()
    health = _check_auth_health()
    file_capability = _file_ingestion_capability()
    capabilities: dict[str, Any] = {
        "auth": {
            "file_status": health["status"],
            "live_ok": False,
            "message": health["message"],
        },
        "notebooks": {"ok": False},
        "sources": {"checked": False},
        "text_source_write": {"ok": False, "probe": "not_run"},
        "file_ingestion": file_capability,
    }
    blocking_issues: list[dict[str, str]] = []

    try:
        client = await get_client()
        notebooks = await client.notebooks.list()
        capabilities["auth"]["live_ok"] = True
        capabilities["notebooks"] = {"ok": True, "count": len(notebooks)}
        capabilities["text_source_write"] = {
            "ok": True,
            "probe": "not_run",
            "message": "Live auth succeeded; add_text_source should be available.",
        }
        if notebook_id:
            sources = await client.sources.list(notebook_id)
            capabilities["sources"] = {
                "checked": True,
                "notebook_id": notebook_id,
                **_source_readiness_summary(sources),
            }
    except Exception as exc:
        message = _live_auth_failure_message(exc)
        capabilities["auth"]["message"] = message
        blocking_issues.append({"code": "LIVE_AUTH_FAILED", "message": message})

    ready_for_read = bool(capabilities["auth"]["live_ok"] and capabilities["notebooks"]["ok"])
    ready_for_text_write = bool(capabilities["text_source_write"]["ok"])
    ready_for_file_ingestion = bool(ready_for_read and file_capability["ok"])
    if blocking_issues:
        overall_status = "blocked"
    elif ready_for_read and ready_for_text_write:
        overall_status = "ready" if ready_for_file_ingestion else "degraded"
    else:
        overall_status = "degraded"

    recommended_workflow = [
        {"tool": "check_mcp_readiness", "args": {"notebook_id": notebook_id}},
        {"tool": "get_or_create_notebook", "when": "ready_for_read is true"},
        {"tool": "add_text_source", "when": "ready_for_text_write is true"},
        {"tool": "list_sources", "when": "after each write, verify readiness"},
    ]
    if ready_for_file_ingestion:
        recommended_workflow.append(
            {
                "tool": "add_file",
                "when": "file is under the configured MCP file root",
                "path_template": file_capability["diagnostics"].get("server_import_path"),
            }
        )

    return {
        "overall_status": overall_status,
        "ready_for_read": ready_for_read,
        "ready_for_text_write": ready_for_text_write,
        "ready_for_file_ingestion": ready_for_file_ingestion,
        "capabilities": capabilities,
        "blocking_issues": blocking_issues,
        "recommended_workflow": recommended_workflow,
    }


@mcp.tool()
async def read_framework_manual(
    topic: Literal["strategy", "quiz", "study"] = "strategy",
) -> dict[str, Any]:
    """Deprecated: use ask_framework_manual backed by the Framework Notebook. (ReadOnly)"""
    return {
        "ok": False,
        "deprecated": True,
        "topic": topic,
        "message": (
            "`read_framework_manual` no longer reads local framework files. "
            "Use the MCP tool `ask_framework_manual` after setting "
            "NOTEBOOKLM_FRAMEWORK_NOTEBOOK_ID. It is a tool, not a resource."
        ),
        "next_action": {
            "tool": "ask_framework_manual",
            "args": {
                "question": f"Explain the {topic} workflow and how to use it through MCP.",
                "role": "auto",
            },
        },
    }


@mcp.tool()
async def run_research_team_workflow(url: str, title: str) -> dict[str, Any]:
    """Direct URL Research Workflow: Minnie->Indy->Vera->Reas->Day. (Write)"""
    client = await get_client()
    nb_result = await get_or_create_notebook(title, reuse_existing=True)
    notebook_id = nb_result["notebook_id"]
    existing = await find_existing_source(client, notebook_id, url)
    if existing:
        return with_notebook_url(
            {
                "notebook_id": notebook_id,
                "source_id": existing.id,
                "status": "ready",
                "workflow": "Duplicate found",
            },
            notebook_id,
        )

    source = await client.sources.add_url(notebook_id, url)
    try:
        await client.sources.wait_until_ready(notebook_id, source.id, timeout=90)
        status = "ready"
    except Exception:
        status = "processing_background"

    summary = "Workflow executed."
    if status == "ready":
        result = await client.chat.ask(
            notebook_id,
            "สรุปเนื้อหาแหล่งข้อมูลนี้เป็นภาษาไทยทั้งหมดเท่านั้น "
            "(ห้ามตอบเป็นภาษาฝรั่งเศส อังกฤษ หรือภาษาอื่น). "
            "ถ้าเป็นคำศัพท์เฉพาะให้คงศัพท์แล้วอธิบายเป็นภาษาไทย.",
        )
        summary = getattr(result, "answer", str(result))
    return with_notebook_url(
        {
            "notebook_id": notebook_id,
            "source_id": source.id,
            "status": status,
            "initial_summary": summary,
        },
        notebook_id,
    )


# Bounded LRU-ish cache: oldest keys are evicted once the cap is reached so a
# long-running container can't leak memory through unique idempotency keys.
_IDEMPOTENCY_MAX_ENTRIES = 512
_idempotency_store: OrderedDict[str, dict[str, Any]] = OrderedDict()


def _idempotency_remember(key: str, value: dict[str, Any]) -> None:
    _idempotency_store[key] = value
    _idempotency_store.move_to_end(key)
    while len(_idempotency_store) > _IDEMPOTENCY_MAX_ENTRIES:
        _idempotency_store.popitem(last=False)


@mcp.tool()
async def run_deep_search_workflow(
    query: str,
    notebook_title: str,
    reuse_existing: bool = True,
    dedupe_strategy: Literal["title_exact", "title_normalized"] = "title_normalized",
    idempotency_key: str | None = None,
    locale: str | None = None,
    country_focus: str | None = None,
    include_foreign_news: bool | None = None,
) -> dict[str, Any]:
    """
    PERFORM WEB SEARCH:
    Creates/reuses a notebook and starts a web research task.
    Returns early with notebook_id and task_id so the client can poll.
    (Write)
    """
    if idempotency_key and idempotency_key in _idempotency_store:
        cached = _idempotency_store[idempotency_key]
        return {
            "status": "existing_task_returned",
            "notebook_id": cached.get("notebook_id"),
            "notebook_url": notebook_url(cached.get("notebook_id")),
            "task_id": cached.get("task_id"),
            "created_new": False,
        }

    # 1. Get or Create Notebook
    nb_result = await get_or_create_notebook(notebook_title, reuse_existing, dedupe_strategy)
    notebook_id = nb_result["notebook_id"]

    # 2. Append context to query if requested
    final_query = query
    context_parts = []
    if locale:
        context_parts.append(f"in {locale} language")
    if country_focus:
        context_parts.append(f"focusing on {country_focus}")
    if include_foreign_news is False:
        context_parts.append("exclude foreign news")

    if context_parts:
        final_query = f"{query} ({', '.join(context_parts)})"

    # 3. Start Research
    client = await get_client()
    task = await client.research.start(notebook_id, final_query)
    if not task:
        return {"error": "Failed to start research"}

    task_id = task.get("task_id") if isinstance(task, dict) else getattr(task, "task_id", None)

    result = {
        "status": "in_progress",
        "phase": "research_started",
        "notebook": {
            "id": notebook_id,
            "url": notebook_url(notebook_id),
            "title": notebook_title,
            "created": nb_result.get("created", False),
            "reused": nb_result.get("reused", True),
        },
        "research": {"task_id": task_id, "query": final_query},
        "dedupe": {
            "duplicate_notebooks_found": len(nb_result.get("duplicates", [])),
            "duplicate_sources_skipped": 0,  # Handled in wait_and_import
        },
        "next_action": {
            "tool": "research_wait_and_import",
            "args": {
                "notebook_id": notebook_id,
                "task_id": task_id,
                "timeout_seconds": 300,
                "interval_seconds": 5,
            },
        },
    }

    if idempotency_key:
        _idempotency_remember(idempotency_key, {"notebook_id": notebook_id, "task_id": task_id})

    return with_notebook_url(result, notebook_id)


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
    res = await client.research.poll(notebook_id)

    st = res.get("status", "unknown")
    if st == "completed" or st == "ready":
        res["phase"] = "sources_found_ready_for_import"
        res["estimated_remaining_steps"] = ["research_wait_and_import"]
    elif st == "no_research":
        res["phase"] = "idle"
        res["estimated_remaining_steps"] = []
    else:
        res["phase"] = "discovering_sources"
        res["estimated_remaining_steps"] = ["wait_for_discovery", "import_sources"]

    if "sources" in res:
        res["sources_found"] = len(res["sources"])

    return res


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

            # Perform Source Deduplication (normalized, consistent with
            # find_existing_source so trailing-slash/scheme variants dedupe).
            existing_sources = await client.sources.list(notebook_id)
            existing_urls = {normalize_url(s.url) for s in existing_sources if s.url}

            incoming_sources = last["sources"]
            sources_to_import = []
            duplicates_skipped = []

            for src in incoming_sources:
                url = src.get("url")
                if url and normalize_url(url) in existing_urls:
                    duplicates_skipped.append({"url": url})
                else:
                    sources_to_import.append(src)

            imported = []
            try:
                if sources_to_import:
                    imported = await client.research.import_sources(
                        notebook_id, task_id, sources_to_import
                    )
            except ValidationError as e:
                return {"error": str(e), "status": st}

            return {
                "status": "imported",
                "task_id": task_id,
                "sources_found": len(incoming_sources),
                "sources_imported": len(imported),
                "sources_skipped_duplicate": len(duplicates_skipped),
                "duplicates": duplicates_skipped,
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
    return [{"id": nb.id, "title": nb.title, "url": notebook_url(nb.id)} for nb in notebooks]


@mcp.tool()
async def create_notebook(title: str) -> dict[str, Any]:
    """Create a new notebook. (Write)"""
    client = await get_client()
    nb = await client.notebooks.create(title)
    return {"id": nb.id, "title": nb.title, "url": notebook_url(nb.id)}


@mcp.tool()
async def get_or_create_notebook(
    title: str,
    reuse_existing: bool = True,
    dedupe_strategy: Literal["title_exact", "title_normalized"] = "title_normalized",
) -> dict[str, Any]:
    """Get an existing notebook by title, or create a new one. (Write)"""
    client = await get_client()

    if not reuse_existing:
        nb = await client.notebooks.create(title)
        return with_notebook_url(
            {"notebook_id": nb.id, "created": True, "reused": False, "duplicates": []}, nb.id
        )

    notebooks = await client.notebooks.list()

    def _normalize(t: str) -> str:
        return re.sub(r"\s+", " ", t.lower()).strip()

    target_title = title if dedupe_strategy == "title_exact" else _normalize(title)

    matches = []
    for nb in notebooks:
        nb_title = nb.title if dedupe_strategy == "title_exact" else _normalize(nb.title)
        if nb_title == target_title:
            matches.append(nb)

    if not matches:
        nb = await client.notebooks.create(title)
        return with_notebook_url(
            {"notebook_id": nb.id, "created": True, "reused": False, "duplicates": []}, nb.id
        )

    # Best match: Just pick the first one from API (usually the most recently used)
    best_match = matches[0]
    duplicate_ids = [nb.id for nb in matches if nb.id != best_match.id]

    return with_notebook_url(
        {
            "notebook_id": best_match.id,
            "created": False,
            "reused": True,
            "duplicates": duplicate_ids,
        },
        best_match.id,
    )


@mcp.tool()
async def delete_notebook(
    notebook_id: str,
    dry_run: bool = True,
    confirm: bool = False,
    verify: bool = True,
) -> dict[str, Any]:
    """Delete a notebook. Defaults to dry-run and requires confirm=True. (Destructive)"""
    client = await get_client()
    notebooks = await client.notebooks.list()
    notebook = next((nb for nb in notebooks if nb.id == notebook_id), None)
    if notebook is None:
        return with_notebook_url(
            {"result": False, "error": "Notebook not found", "notebook_id": notebook_id},
            notebook_id,
        )

    if dry_run or not confirm:
        return with_notebook_url(
            {
                "dry_run": True,
                "would_delete": {
                    "id": notebook.id,
                    "title": notebook.title,
                    "url": notebook_url(notebook.id),
                },
                "next_action": {
                    "tool": "delete_notebook",
                    "args": {
                        "notebook_id": notebook_id,
                        "dry_run": False,
                        "confirm": True,
                        "verify": verify,
                    },
                },
            },
            notebook_id,
        )

    try:
        await client.notebooks.delete(notebook_id)
        result = {
            "result": True,
            "deleted": {
                "id": notebook.id,
                "title": notebook.title,
                "url": notebook_url(notebook.id),
            },
        }
        if verify:
            notebooks = await client.notebooks.list()
            still_exists = any(nb.id == notebook_id for nb in notebooks)
            result["verified"] = True
            result["still_exists"] = [notebook_id] if still_exists else []
        return with_notebook_url(result, notebook_id)
    except Exception as e:
        return {"result": False, "error": str(e)}


@mcp.tool()
async def delete_notebooks(
    notebook_ids: list[str], dry_run: bool = True, confirm: bool = False, verify: bool = False
) -> dict[str, Any]:
    """Batch delete notebooks. Defaults to dry-run. Requires confirm=True to actually delete. (Destructive)"""
    client = await get_client()
    notebooks = await client.notebooks.list()

    to_delete = [nb for nb in notebooks if nb.id in notebook_ids]

    if dry_run or not confirm:
        return {
            "dry_run": True,
            "would_delete_count": len(to_delete),
            "would_delete": [
                {"id": nb.id, "title": nb.title, "url": notebook_url(nb.id)} for nb in to_delete
            ],
            "next_action": {
                "tool": "delete_notebooks",
                "args": {
                    "notebook_ids": notebook_ids,
                    "dry_run": False,
                    "confirm": True,
                    "verify": verify,
                },
            },
        }

    deleted_count = 0
    failed = []
    deleted_info = []
    for nb in to_delete:
        try:
            await client.notebooks.delete(nb.id)
            deleted_count += 1
            deleted_info.append({"id": nb.id, "title": nb.title, "url": notebook_url(nb.id)})
        except Exception as e:
            failed.append({"id": nb.id, "error": str(e)})

    result = {
        "requested": len(notebook_ids),
        "deleted_count": deleted_count,
        "failed_count": len(failed),
        "deleted": deleted_info,
        "failed": failed,
    }

    if verify:
        remaining = await client.notebooks.list()
        still_exists = [id for id in notebook_ids if any(nb.id == id for nb in remaining)]
        result["verified"] = True
        result["still_exists"] = still_exists

    return result


@mcp.tool()
async def delete_notebooks_by_title(
    title: str,
    match: Literal["exact", "normalized_exact", "prefix", "contains", "regex"] = "exact",
    dry_run: bool = True,
    confirm: bool = False,
    verify: bool = False,
    confirm_phrase: str | None = None,
) -> dict[str, Any]:
    """Delete notebooks matching a title. Defaults to dry-run. Requires confirm=True. (Destructive)"""
    client = await get_client()
    notebooks = await client.notebooks.list()

    def _normalize(t: str) -> str:
        return re.sub(r"\s+", " ", t.lower()).strip()

    matched = []
    for nb in notebooks:
        if (
            (match == "exact" and nb.title == title)
            or (match == "normalized_exact" and _normalize(nb.title) == _normalize(title))
            or (match == "prefix" and nb.title.startswith(title))
            or (match == "contains" and title in nb.title)
            or (match == "regex" and re.search(title, nb.title))
        ):
            matched.append(nb)

    if dry_run or not confirm:
        return {
            "dry_run": True,
            "match": match,
            "matched_count": len(matched),
            "matched_notebooks": [
                {"id": nb.id, "title": nb.title, "url": notebook_url(nb.id)} for nb in matched
            ],
            "next_action": {
                "tool": "delete_notebooks_by_title",
                "args": {
                    "title": title,
                    "match": match,
                    "dry_run": False,
                    "confirm": True,
                    "verify": verify,
                    "confirm_phrase": confirm_phrase,
                },
            },
        }

    if match in ("contains", "regex") and confirm_phrase != title:
        return {
            "error": (
                f"Match mode '{match}' is broad and requires confirm_phrase to exactly "
                f"equal the title/pattern ({title!r}) before deletion will run."
            ),
            "matched_count": len(matched),
        }

    return await delete_notebooks(
        notebook_ids=[nb.id for nb in matched], dry_run=False, confirm=True, verify=verify
    )


@mcp.tool()
async def archive_notebooks(
    notebook_ids: list[str], reason: str = "cleanup", dry_run: bool = True
) -> dict[str, Any]:
    """Archive notebooks by renaming them instead of hard deleting. (Write)"""
    client = await get_client()
    notebooks = await client.notebooks.list()

    to_archive = [nb for nb in notebooks if nb.id in notebook_ids]
    today = datetime.datetime.now().strftime("%Y-%m-%d")

    if dry_run:
        return {
            "dry_run": True,
            "would_archive_count": len(to_archive),
            "would_archive": [
                {
                    "id": nb.id,
                    "url": notebook_url(nb.id),
                    "old_title": nb.title,
                    "new_title": f"[ARCHIVED {today}] {nb.title}",
                }
                for nb in to_archive
            ],
        }

    archived = []
    failed = []

    for nb in to_archive:
        try:
            new_title = f"[ARCHIVED {today}] {nb.title}"
            await client.notebooks.rename(nb.id, new_title)
            archived.append({"id": nb.id, "title": new_title, "url": notebook_url(nb.id)})
        except Exception as e:
            failed.append({"id": nb.id, "error": str(e)})

    return {"archived": archived, "failed": failed}


@mcp.tool()
async def rename_notebook(notebook_id: str, new_title: str) -> dict[str, Any]:
    """Rename a notebook (CLI: ``notebook rename``). (Write)"""
    client = await get_client()
    nb = await client.notebooks.rename(notebook_id, new_title)
    return {"id": nb.id, "title": nb.title, "url": notebook_url(nb.id)}


@mcp.tool()
async def find_duplicate_notebooks(
    strategy: Literal["title_exact", "title_normalized"] = "title_normalized",
    include_counts: bool = True,
) -> dict[str, Any]:
    """Find duplicate notebooks based on title. (ReadOnly)"""
    client = await get_client()
    notebooks = await client.notebooks.list()

    def _normalize(t: str) -> str:
        return re.sub(r"\s+", " ", t.lower()).strip()

    groups: dict[str, list[dict[str, Any]]] = {}
    for nb in notebooks:
        key = nb.title if strategy == "title_exact" else _normalize(nb.title)
        if key not in groups:
            groups[key] = []

        nb_info = {
            "id": nb.id,
            "url": notebook_url(nb.id),
            "title": nb.title,
            "created_at": getattr(nb, "created_at", None),
            "sources_count": getattr(nb, "sources_count", 0),
        }
        groups[key].append(nb_info)

    duplicates = []
    for key, group in groups.items():
        if len(group) > 1:
            # Sort to recommend keeping the one with the most sources, then latest created
            group.sort(
                key=lambda x: (x.get("sources_count", 0), x.get("created_at") or ""), reverse=True
            )
            duplicates.append(
                {
                    "normalized_title": key,
                    "count": len(group),
                    "recommended_keep": group[0]["id"],
                    "notebooks": group,
                }
            )

    return {"duplicate_groups": duplicates}


@mcp.tool()
async def cleanup_duplicate_notebooks(
    strategy: Literal["title_exact", "title_normalized"] = "title_normalized",
    mode: Literal["delete_empty_duplicates"] = "delete_empty_duplicates",
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Safe cleanup of duplicate notebooks. Defaults to dry-run. Requires confirm=True. (Write)"""
    duplicates_res = await find_duplicate_notebooks(strategy=strategy, include_counts=True)
    duplicate_groups = duplicates_res.get("duplicate_groups", [])

    would_delete = []
    would_keep = []

    for group in duplicate_groups:
        notebooks = group["notebooks"]
        recommended_keep = group["recommended_keep"]

        for nb in notebooks:
            if nb["id"] == recommended_keep:
                would_keep.append(
                    {
                        "id": nb["id"],
                        "url": notebook_url(nb["id"]),
                        "reason": "has most sources / latest",
                    }
                )
                continue

            if mode == "delete_empty_duplicates":
                if nb.get("sources_count", 0) == 0:
                    would_delete.append(
                        {
                            "id": nb["id"],
                            "url": notebook_url(nb["id"]),
                            "title": nb["title"],
                            "reason": "empty duplicate",
                        }
                    )
                else:
                    would_keep.append(
                        {
                            "id": nb["id"],
                            "url": notebook_url(nb["id"]),
                            "reason": f"not empty (sources: {nb.get('sources_count')})",
                        }
                    )

    if dry_run or not confirm:
        return {
            "dry_run": True,
            "mode": mode,
            "groups_found": len(duplicate_groups),
            "would_delete": would_delete,
            "would_keep": would_keep,
            "next_action": "call again with dry_run=False and confirm=True to execute deletion",
        }

    client = await get_client()
    deleted = []
    failed = []
    for item in would_delete:
        try:
            await client.notebooks.delete(item["id"])
            deleted.append(item)
        except Exception as e:
            failed.append({"id": item["id"], "error": str(e)})

    return {"status": "completed", "deleted": deleted, "failed": failed, "kept": would_keep}


@mcp.tool()
async def get_notebook_summary(notebook_id: str) -> dict[str, Any]:
    """Get notebook summary and suggested topics. (ReadOnly)"""
    client = await get_client()
    description = await client.notebooks.get_description(notebook_id)
    return with_notebook_url(
        {
            "notebook_id": notebook_id,
            "summary": description.summary,
            "topics": [{"question": t.question} for t in description.suggested_topics],
        },
        notebook_id,
    )


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
async def list_sources(notebook_id: str) -> dict[str, Any]:
    """List all sources in a notebook. (ReadOnly)"""
    client = await get_client()
    sources = await client.sources.list(notebook_id)
    rows = [
        {
            "id": s.id,
            "title": s.title,
            "status": source_status_to_str(s.status),
            "url": getattr(s, "url", None),
            "created_at": getattr(s, "created_at", None),
            "source_type": getattr(s, "source_type", "unknown"),
        }
        for s in sources
    ]
    return with_notebook_url(
        {
            "notebook_id": notebook_id,
            "count": len(rows),
            "ready_count": sum(1 for row in rows if row["status"] == "ready"),
            "processing_count": sum(
                1 for row in rows if row["status"] in {"processing", "pending", "in_progress"}
            ),
            "error_count": sum(1 for row in rows if row["status"] == "error"),
            "sources": rows,
        },
        notebook_id,
    )


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
        return with_notebook_url(
            {
                "id": existing.id,
                "status": source_status_to_str(existing.status),
                "note": "Duplicate blocked – source already exists.",
            },
            notebook_id,
        )
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
                client.sources.wait_until_ready(notebook_id, source.id, timeout=55.0),
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
    return with_notebook_url(result, notebook_id)


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
            return with_notebook_url(
                {"status": "error", "is_ready": False, "note": "Source not found"},
                notebook_id,
            )
        last_status = source_status_to_str(source.status)
        if source.is_ready:
            return with_notebook_url(
                {
                    "source_id": source_id,
                    "status": "ready",
                    "is_ready": True,
                    "title": source.title,
                },
                notebook_id,
            )
        if source.is_error:
            return with_notebook_url(
                {"source_id": source_id, "status": "error", "is_ready": False},
                notebook_id,
            )
        remaining = deadline - time.monotonic()
        await asyncio.sleep(min(interval, max(1.0, remaining)))
        interval = min(interval * 1.5, 15.0)
    return with_notebook_url(
        {
            "source_id": source_id,
            "status": "timeout",
            "is_ready": False,
            "last_status": last_status,
            "note": f"Still processing after {timeout_seconds}s. Try again later.",
        },
        notebook_id,
    )


@mcp.tool()
async def add_text_source(notebook_id: str, title: str, text: str) -> dict[str, Any]:
    """Add text source. (Write)"""
    client = await get_client()
    if await find_existing_title(client, notebook_id, title, kind="source"):
        return with_notebook_url({"error": f"Title '{title}' already exists."}, notebook_id)
    source = await client.sources.add_text(notebook_id, title, text)
    return with_notebook_url(
        {"id": source.id, "title": source.title, "status": source_status_to_str(source.status)},
        notebook_id,
    )


@mcp.tool()
async def refresh_source(notebook_id: str, source_id: str) -> dict[str, Any]:
    """Refresh an existing source. (Write)"""
    client = await get_client()
    ok = await client.sources.refresh(notebook_id, source_id)
    return with_notebook_url(
        {"result": bool(ok), "source_id": source_id},
        notebook_id,
    )


@mcp.tool()
async def get_source_fulltext(
    notebook_id: str, source_id: str, extract_clean_content: bool = True
) -> dict[str, Any]:
    """Get the full indexed text of a source. Can attempt to extract and clean main content. (ReadOnly)"""
    client = await get_client()
    ft = await client.sources.get_fulltext(notebook_id, source_id)
    sources = await client.sources.list(notebook_id)
    source = next((s for s in sources if s.id == source_id), None)

    result = {
        "notebook_id": notebook_id,
        "source_id": source_id,
        "title": getattr(source, "title", None),
        "url": getattr(source, "url", None),
        "content": ft.content,
        "raw_text": ft.content,
        "cleaned_text": None,
        "used_clean_extractor": extract_clean_content,
        "clean_extraction_success": False,
        "clean_extraction_error": None,
        "char_count_raw": len(ft.content),
        "char_count_cleaned": None,
        "char_count": ft.char_count,
        "boilerplate_reduction_ratio": None,
        "boilerplate_removed": False,
    }

    if extract_clean_content:
        try:
            import trafilatura

            # Using trafilatura to extract main text from raw HTML/Markdown content
            cleaned = trafilatura.extract(ft.content, include_comments=False, include_tables=True)
            if cleaned:
                result["cleaned_text"] = cleaned
                result["clean_extraction_success"] = True
                result["char_count_cleaned"] = len(cleaned)
                result["boilerplate_reduction_ratio"] = 1 - (len(cleaned) / max(1, len(ft.content)))
                result["boilerplate_removed"] = True
                result["content_quality_score"] = len(cleaned) / max(1, len(ft.content))
            else:
                result["clean_extraction_error"] = "NO_MAIN_CONTENT_FOUND"
        except Exception as e:
            result["clean_extraction_error"] = str(e)

    return with_notebook_url(result, notebook_id)


@mcp.tool()
async def delete_source(
    notebook_id: str,
    source_id: str,
    dry_run: bool = True,
    confirm: bool = False,
    verify: bool = True,
) -> dict[str, Any]:
    """Delete a source. Defaults to dry-run and requires confirm=True. (Destructive)"""
    client = await get_client()
    sources = await client.sources.list(notebook_id)
    source = next((s for s in sources if s.id == source_id), None)
    if source is None:
        return with_notebook_url(
            {"result": False, "error": "Source not found", "source_id": source_id},
            notebook_id,
        )

    if dry_run or not confirm:
        return with_notebook_url(
            {
                "dry_run": True,
                "would_delete": _source_to_delete_info(source),
                "next_action": {
                    "tool": "delete_source",
                    "args": {
                        "notebook_id": notebook_id,
                        "source_id": source_id,
                        "dry_run": False,
                        "confirm": True,
                        "verify": verify,
                    },
                },
            },
            notebook_id,
        )

    deleted = await client.sources.delete(notebook_id, source_id)
    result: dict[str, Any] = {"result": bool(deleted), "deleted": _source_to_delete_info(source)}
    if verify:
        remaining = await client.sources.list(notebook_id)
        result["verified"] = True
        result["still_exists"] = [source_id] if any(s.id == source_id for s in remaining) else []
    return with_notebook_url(result, notebook_id)


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
    return with_notebook_url(
        {"id": source.id, "title": source.title, "status": source_status_to_str(source.status)},
        notebook_id,
    )


FILE_ROOT_ENV = "NOTEBOOKLM_MCP_FILE_ROOT"
DEFAULT_DOCKER_FILE_ROOT = "/imports"
DEFAULT_HOST_IMPORT_DIR = "./mcp_imports"


def _file_root_diagnostics(file_path: str, root_env: str | None) -> dict[str, Any]:
    return {
        "file_root_env": FILE_ROOT_ENV,
        "requested_path": file_path,
        "allowed_root": str(Path(root_env).resolve()) if root_env else None,
        "host_import_dir": DEFAULT_HOST_IMPORT_DIR
        if root_env == DEFAULT_DOCKER_FILE_ROOT
        else None,
        "server_import_path": DEFAULT_DOCKER_FILE_ROOT
        if root_env == DEFAULT_DOCKER_FILE_ROOT
        else None,
    }


def _add_file_error_response(file_path: str, code: str, message: str) -> dict[str, Any]:
    root_env = os.environ.get(FILE_ROOT_ENV)
    diagnostics = _file_root_diagnostics(file_path, root_env)
    next_message = (
        "Copy the file into ./mcp_imports on the host, then call add_file with "
        f"`{DEFAULT_DOCKER_FILE_ROOT}/<filename>`."
        if root_env == DEFAULT_DOCKER_FILE_ROOT
        else (
            f"Copy the file into the directory configured by {FILE_ROOT_ENV}, then call "
            "add_file with that server-side path."
        )
        if root_env
        else f"Set {FILE_ROOT_ENV} to a server directory, or use add_text_source."
    )
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
        "diagnostics": diagnostics,
        "next_action": {
            "message": next_message,
            "fallback_tool": "add_text_source",
        },
    }


def _resolve_allowed_file(file_path: str) -> Path:
    """Resolve *file_path* and confirm it lives inside the configured upload root.

    The MCP server can be exposed publicly (e.g. via a Cloudflare tunnel), so an
    unconstrained ``add_file`` would let any connected client read arbitrary
    server files — including ``storage_state.json`` (auth cookies). We therefore
    require an explicit allow-list root via ``NOTEBOOKLM_MCP_FILE_ROOT`` and
    reject anything that escapes it (including symlink targets).
    """
    root_env = os.environ.get(FILE_ROOT_ENV)
    if not root_env:
        raise ValidationError("FILE_ROOT_NOT_CONFIGURED")
    root = Path(root_env).resolve()
    resolved = Path(file_path).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValidationError("FILE_OUTSIDE_ALLOWED_ROOT")
    if not resolved.is_file():
        raise ValidationError("FILE_NOT_FOUND_INSIDE_ALLOWED_ROOT")
    return resolved


@mcp.tool()
async def add_file(
    notebook_id: str,
    file_path: str,
) -> dict[str, Any]:
    """Add a file from the MCP server's own filesystem as a source. (Write)

    IMPORTANT: this reads the *server's* disk, not the calling agent's
    sandbox. A ChatGPT/Claude path such as ``/mnt/data/foo.md`` does NOT
    exist on the server — use ``add_text_source`` to ingest content you
    generated, or ``add_drive`` for Google Drive files.

    Only files inside the directory named by the ``NOTEBOOKLM_MCP_FILE_ROOT``
    environment variable may be added. If that variable is unset, this tool is
    disabled.
    """
    client = await get_client()
    try:
        safe_path = _resolve_allowed_file(file_path)
    except ValidationError as e:
        code = str(e)
        messages = {
            "FILE_ROOT_NOT_CONFIGURED": (
                "add_file is disabled because NOTEBOOKLM_MCP_FILE_ROOT is not configured. "
                "This tool reads files from the MCP server filesystem, not a chat sandbox."
            ),
            "FILE_OUTSIDE_ALLOWED_ROOT": (
                "file_path is outside the configured MCP file root. Chat sandbox paths "
                "such as /mnt/data are not visible to the MCP server."
            ),
            "FILE_NOT_FOUND_INSIDE_ALLOWED_ROOT": (
                "No readable file exists at that server-side path inside the configured "
                "MCP file root."
            ),
        }
        return with_notebook_url(
            _add_file_error_response(file_path, code, messages.get(code, str(e))),
            notebook_id,
        )
    source = await client.sources.add_file(notebook_id, str(safe_path))
    return with_notebook_url(
        {"id": source.id, "title": source.title, "status": source_status_to_str(source.status)},
        notebook_id,
    )


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
    return _status_to_generation_response(notebook_id, status)


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
    return _status_to_generation_response(notebook_id, status)


@mcp.tool()
async def generate_cinematic_video(
    notebook_id: str, instructions: str | None = None
) -> dict[str, Any]:
    """Generate a Cinematic Video. (Write)"""
    client = await get_client()
    status = await client.artifacts.generate_cinematic_video(notebook_id, instructions=instructions)
    return _status_to_generation_response(notebook_id, status)


@mcp.tool()
async def generate_report(
    notebook_id: str,
    format: Literal["BRIEFING_DOC", "STUDY_GUIDE", "BLOG_POST", "CUSTOM"] = "BRIEFING_DOC",
    extra_instructions: str | None = None,
) -> dict[str, Any]:
    """Generate a specialized report. (Write)"""
    client = await get_client()
    status = await client.artifacts.generate_report(
        notebook_id, report_format=ReportFormat[format], extra_instructions=extra_instructions
    )
    return _status_to_generation_response(notebook_id, status)


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
    return _status_to_generation_response(notebook_id, status)


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
    return _status_to_generation_response(notebook_id, status)


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
    return _status_to_generation_response(notebook_id, status)


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
    return _status_to_generation_response(notebook_id, status)


@mcp.tool()
async def generate_data_table(notebook_id: str, instructions: str) -> dict[str, Any]:
    """Generate a structured data table. (Write)"""
    client = await get_client()
    status = await client.artifacts.generate_data_table(notebook_id, instructions=instructions)
    return _status_to_generation_response(notebook_id, status)


@mcp.tool()
async def generate_mind_map(notebook_id: str) -> dict[str, Any]:
    """Generate interactive Mind Map saved as a note. (Write)"""
    client = await get_client()
    result = await client.artifacts.generate_mind_map(notebook_id)
    return with_notebook_url(result, notebook_id)


@mcp.tool()
async def poll_artifact_status(notebook_id: str, task_id: str) -> dict[str, Any]:
    """Check task completion. (ReadOnly)"""
    client = await get_client()
    status = await client.artifacts.poll_status(notebook_id, task_id)
    result = _status_to_generation_response(notebook_id, status)
    if status.is_complete:
        artifact = await client.artifacts.get(notebook_id, task_id)
        if artifact is not None:
            artifact_dict = _artifact_to_dict(artifact)
            result["artifact"] = artifact_dict
            result["next_action"] = {
                "tool": "get_artifact_content",
                "args": {"notebook_id": notebook_id, "artifact_id": artifact.id},
            }
    return result


@mcp.tool()
async def list_artifacts(notebook_id: str) -> dict[str, Any]:
    """List AI-generated artifacts in the notebook (CLI: ``artifact list``). (ReadOnly)"""
    client = await get_client()
    artifacts = await client.artifacts.list(notebook_id)
    rows = []
    for artifact in artifacts:
        row = _artifact_to_dict(artifact)
        kind = row["kind"]
        row["supports_content_retrieval"] = _artifact_supports_content_retrieval(kind)
        row["recommended_content_format"] = _artifact_content_format_hint(kind)
        rows.append(row)
    return with_notebook_url(
        {"notebook_id": notebook_id, "count": len(rows), "artifacts": rows}, notebook_id
    )


@mcp.tool()
async def get_artifact(notebook_id: str, artifact_id: str) -> dict[str, Any]:
    """Get one artifact by id (metadata; CLI: ``artifact get``). (ReadOnly)"""
    client = await get_client()
    artifact = await client.artifacts.get(notebook_id, artifact_id)
    if artifact is None:
        return with_notebook_url({"error": "Artifact not found"}, notebook_id)
    return with_notebook_url(_artifact_to_dict(artifact), notebook_id)


@mcp.tool()
async def get_artifact_content(
    notebook_id: str,
    artifact_id: str,
    format: Literal["auto", "markdown", "json", "csv", "plain_text"] = "auto",
) -> dict[str, Any]:
    """Retrieve the text/markdown/json content of a supported artifact (Report, Quiz, Flashcards, Data Table). (ReadOnly)"""
    client = await get_client()
    artifact = await client.artifacts.get(notebook_id, artifact_id)

    if not artifact:
        return with_notebook_url({"error": "Artifact not found"}, notebook_id)

    if artifact.status != 3:  # ArtifactStatus.COMPLETED
        return with_notebook_url(
            {"error": f"Artifact not complete (status={artifact.status})"},
            notebook_id,
        )

    kind = artifact.kind.value if hasattr(artifact.kind, "value") else str(artifact.kind)
    content = ""
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
            tmp_path = tmp.name

        if kind == "report":
            await client.artifacts.download_report(notebook_id, tmp_path, artifact_id=artifact_id)
        elif kind == "quiz":
            await client.artifacts.download_quiz(notebook_id, tmp_path, artifact_id=artifact_id)
        elif kind == "flashcards":
            await client.artifacts.download_flashcards(
                notebook_id, tmp_path, artifact_id=artifact_id
            )
        elif kind == "data_table":
            await client.artifacts.download_data_table(
                notebook_id, tmp_path, artifact_id=artifact_id
            )
        elif kind == "mind_map":
            await client.artifacts.download_mind_map(notebook_id, tmp_path, artifact_id=artifact_id)
        else:
            return with_notebook_url(
                {"error": f"Artifact type '{kind}' content retrieval not supported via MCP yet."},
                notebook_id,
            )

        with open(tmp_path, encoding="utf-8") as f:
            content = f.read()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    converted = _coerce_artifact_content(content, kind, format)
    return with_notebook_url(
        {
            "artifact_id": artifact_id,
            "kind": kind,
            **converted,
        },
        notebook_id,
    )


# --- Notes & Chat ---


@mcp.tool()
async def ask_question(
    notebook_id: str,
    question: str,
    response_format: Literal["text", "json"] = "text",
    strict_json: bool = False,
    include_citations: bool = False,
    citations_mode: Literal["inline", "separate"] = "inline",
) -> str | dict[str, Any]:
    """Ask sources via NotebookLM RAG engine. (ReadOnly)"""
    client = await get_client()

    final_question = question
    if response_format == "json":
        final_question += (
            "\n\nReply in strictly valid JSON format only, without markdown code fences."
        )
        if strict_json:
            final_question += " Do not include citation markers like [1] or [2] inside the JSON values. Output numbers as actual numbers."

    result = await client.chat.ask(notebook_id, final_question)

    answer_text = getattr(result, "answer", str(result))

    if response_format == "json" and strict_json:
        # Strip markdown fences if present
        cleaned = re.sub(r"^```json\s*", "", answer_text, flags=re.IGNORECASE)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()
        try:
            parsed = json.loads(cleaned)
            output: dict[str, Any] = {
                "ok": True,
                "notebook_id": notebook_id,
                "notebook_url": notebook_url(notebook_id),
                "answer": parsed,
                "result": {"answer": parsed},
                "repair_attempted": False,
                "repair_success": False,
            }
        except json.JSONDecodeError:
            return {
                "ok": False,
                "notebook_id": notebook_id,
                "notebook_url": notebook_url(notebook_id),
                "error": {
                    "code": "STRICT_JSON_PARSE_FAILED",
                    "message": "The model response could not be parsed as valid JSON.",
                },
                "raw_answer": answer_text,
                "repair_attempted": False,
                "repair_success": False,
            }

        if include_citations and citations_mode == "separate":
            refs = getattr(result, "references", [])
            output["citations"] = [
                {
                    "source_id": r.source_id,
                    "citation_number": r.citation_number,
                    "quote": r.cited_text,
                }
                for r in refs
            ]
        return with_notebook_url(output, notebook_id)

    return answer_text


@mcp.tool()
async def list_notes(notebook_id: str) -> list[dict[str, Any]]:
    """List text notes. (ReadOnly)"""
    client = await get_client()
    notes = await client.notes.list(notebook_id)
    return [
        {"id": n.id, "title": n.title, "notebook_url": notebook_url(notebook_id)} for n in notes
    ]


@mcp.tool()
async def create_note(notebook_id: str, title: str, content: str) -> dict[str, Any]:
    """Create a new note. Check for duplicates first. (Write)"""
    client = await get_client()
    if await find_existing_title(client, notebook_id, title, kind="note"):
        return with_notebook_url({"error": f"Note '{title}' already exists."}, notebook_id)
    note = await client.notes.create(notebook_id, title, content)
    return with_notebook_url({"id": note.id, "title": note.title}, notebook_id)


@mcp.tool()
async def get_note(notebook_id: str, note_id: str) -> dict[str, Any]:
    """Read a note's title and body (CLI: ``note get``). (ReadOnly)"""
    client = await get_client()
    note = await client.notes.get(notebook_id, note_id)
    if note is None:
        return with_notebook_url({"error": "Note not found"}, notebook_id)
    return with_notebook_url(
        {"id": note.id, "title": note.title, "content": note.content},
        notebook_id,
    )


@mcp.tool()
async def rename_note(notebook_id: str, note_id: str, new_title: str) -> dict[str, Any]:
    """Rename a note (CLI: ``note rename``). (Write)"""
    client = await get_client()
    note = await client.notes.get(notebook_id, note_id)
    if note is None:
        return with_notebook_url({"error": "Note not found"}, notebook_id)
    await client.notes.update(notebook_id, note_id, note.content, new_title)
    return with_notebook_url({"id": note_id, "title": new_title}, notebook_id)


@mcp.tool()
async def delete_note(
    notebook_id: str,
    note_id: str,
    dry_run: bool = True,
    confirm: bool = False,
    verify: bool = True,
) -> dict[str, Any]:
    """Delete a note. Defaults to dry-run and requires confirm=True. (Destructive)"""
    client = await get_client()
    note = await client.notes.get(notebook_id, note_id)
    if note is None:
        return with_notebook_url(
            {"result": False, "error": "Note not found", "note_id": note_id},
            notebook_id,
        )

    if dry_run or not confirm:
        return with_notebook_url(
            {
                "dry_run": True,
                "would_delete": _note_to_delete_info(note),
                "next_action": {
                    "tool": "delete_note",
                    "args": {
                        "notebook_id": notebook_id,
                        "note_id": note_id,
                        "dry_run": False,
                        "confirm": True,
                        "verify": verify,
                    },
                },
            },
            notebook_id,
        )

    deleted = await client.notes.delete(notebook_id, note_id)
    result: dict[str, Any] = {"result": bool(deleted), "deleted": _note_to_delete_info(note)}
    if verify:
        remaining = await client.notes.get(notebook_id, note_id)
        result["verified"] = True
        result["still_exists"] = [note_id] if remaining is not None else []
    return with_notebook_url(result, notebook_id)


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

    # --- Startup: auth health check ---
    auth_info = _check_auth_health()
    if auth_info["status"] == "expired":
        logger.error("[startup] %s", auth_info["message"])
    elif auth_info["status"] in ("warn", "unknown"):
        logger.warning("[startup] %s", auth_info["message"])
    else:
        logger.info("[startup] %s", auth_info["message"])

    # The file-level check above only proves that storage_state.json exists and
    # has unexpired cookie timestamps. A live token fetch is the honest signal:
    # Google can revoke cookies server-side while their local expiry still looks
    # healthy.
    try:
        await get_client()
        logger.info("[startup] Live NotebookLM auth probe succeeded.")
    except Exception as exc:
        logger.error("[startup] Live NotebookLM auth probe failed: %s", exc)

    # --- Startup: launch background auth watcher (every 1 h) ---
    watcher_task = asyncio.create_task(_auth_watcher(interval_seconds=3600))
    watcher_task.set_name("auth-watcher")

    # --- Startup: launch session token refresh (every 10 min) ---
    # Keeps idle f.sid / csrf_token warm; the core layer also self-heals
    # stale tokens via the code-16 refresh+retry path.
    refresh_task = asyncio.create_task(_session_refresh_task(interval_seconds=600))
    refresh_task.set_name("session-refresh")

    # Start the Streamable HTTP session manager task group.
    try:
        if mcp._session_manager:
            async with mcp._session_manager.run():
                logger.info("✅ Streamable HTTP session manager started")
                yield
        else:
            logger.warning("⚠️ Streamable HTTP session manager is None, skipping run()")
            yield
    finally:
        # Shutdown: cancel background tasks
        watcher_task.cancel()
        refresh_task.cancel()
        for task in (watcher_task, refresh_task):
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Shutdown: clean up the NotebookLM client
        global _client
        if _client:
            await _client.__aexit__(None, None, None)


app = FastAPI(title="NotebookLM Framework MCP", lifespan=lifespan)


# --- Ultra-Stable Proxy Handlers ---


@app.get("/health")
async def health(request: Request):
    """Health check endpoint reporting available MCP transports and auth status."""
    auth_info = _check_auth_health()
    base = str(request.base_url).rstrip("/")
    return {
        "status": "ok",
        "auth": auth_info,
        "transports": {
            "streamable_http": "/mcp",
            "sse_legacy": "/sse",
        },
        "clients": {
            "claude_ai": f"{base}/mcp",
            "chatgpt": f"{base}/sse/",
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
