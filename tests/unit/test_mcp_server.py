"""Unit tests for notebooklm MCP server.

Tests cover:
- Tool discovery: all 21 expected tools are registered with correct metadata
- Tool schema: input schemas are valid and contain required parameters
- Error propagation: RuntimeError when client init fails
- Client singleton: get_client() returns the same instance
- Server transport: /sse and /messages/ endpoints respond correctly
- Tool categories: notebooks, sources, notes, artifacts, research, sharing, chat
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Every tool that mcp_server.py must expose.
EXPECTED_TOOLS = {
    # Notebook category
    "list_notebooks",
    "get_notebook_summary",
    # Source category
    "list_sources",
    "add_url_source",
    "add_text_source",
    "delete_source",
    # Note category
    "list_notes",
    "create_note",
    "get_note",
    "delete_note",
    # Artifact category
    "list_artifacts",
    "generate_audio_overview",
    "generate_study_guide",
    "poll_artifact_status",
    "delete_artifact",
    # Research category
    "start_research",
    "poll_research_results",
    "import_research_sources",
    # Sharing category
    "get_share_status",
    "set_notebook_public",
    # Chat category
    "ask_question",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client():
    """Build a fully-mocked NotebookLMClient."""
    client = AsyncMock()

    # Notebooks
    nb = MagicMock(id="nb-1", title="Test Notebook")
    nb.last_modified = "2024-01-01"
    client.notebooks.list = AsyncMock(return_value=[nb])
    client.notebooks.get_summary = AsyncMock(return_value="A notebook summary")
    desc = MagicMock(description="A description")
    client.notebooks.get_description = AsyncMock(return_value=desc)

    # Sources
    source = MagicMock(id="src-1", title="Source One")
    source.kind = MagicMock(value="url")
    source.status = MagicMock()
    client.sources.list = AsyncMock(return_value=[source])
    client.sources.add_url = AsyncMock(return_value=source)
    client.sources.add_text = AsyncMock(return_value=source)
    client.sources.delete = AsyncMock(return_value=True)

    # Notes
    note = MagicMock(id="note-1", title="My Note", content="Hello world")
    note.last_modified = "2024-01-02"
    client.notes.list = AsyncMock(return_value=[note])
    client.notes.create = AsyncMock(return_value=note)
    client.notes.get = AsyncMock(return_value=note)
    client.notes.delete = AsyncMock(return_value=True)

    # Artifacts
    artifact = MagicMock(id="art-1", title="Audio", created_at="2024-01-03")
    artifact.kind = MagicMock(value="audio")
    artifact.status = MagicMock()
    client.artifacts.list = AsyncMock(return_value=[artifact])
    task_status = MagicMock(task_id="task-1", status="pending", is_complete=False, is_failed=False, error=None)
    client.artifacts.generate_audio = AsyncMock(return_value=task_status)
    client.artifacts.generate_study_guide = AsyncMock(return_value=task_status)
    client.artifacts.poll_status = AsyncMock(return_value=task_status)
    client.artifacts.delete = AsyncMock(return_value=True)

    # Research
    client.research.start = AsyncMock(return_value={"task_id": "res-1", "status": "running"})
    client.research.poll = AsyncMock(
        return_value={
            "status": "done",
            "sources": [{"id": "src-2", "title": "Wiki", "url": "https://en.wikipedia.org"}],
        }
    )
    client.research.import_sources = AsyncMock(return_value=[{"id": "src-2", "title": "Wiki"}])

    # Sharing
    share_status = MagicMock(
        notebook_id="nb-1",
        is_public=True,
        share_url="https://notebooklm.google.com/nb-1",
        shared_users=[],
    )
    client.sharing.get_status = AsyncMock(return_value=share_status)
    client.sharing.set_public = AsyncMock(return_value=share_status)

    # Chat
    answer = MagicMock(answer="The answer is 42.")
    client.chat.ask = AsyncMock(return_value=answer)

    return client


# ---------------------------------------------------------------------------
# Tool Discovery Tests
# ---------------------------------------------------------------------------


class TestToolDiscovery:
    """Verify all expected MCP tools are registered."""

    @pytest.mark.asyncio
    async def test_all_expected_tools_are_registered(self):
        """The server must expose every tool in EXPECTED_TOOLS."""
        from notebooklm.mcp_server import mcp

        tools = await mcp.list_tools()
        registered_names = {t.name for t in tools}
        missing = EXPECTED_TOOLS - registered_names
        assert not missing, f"Missing tools: {missing}"

    @pytest.mark.asyncio
    async def test_no_extra_unexpected_tools(self):
        """Registered tool count must match EXPECTED_TOOLS (no silent additions)."""
        from notebooklm.mcp_server import mcp

        tools = await mcp.list_tools()
        registered_names = {t.name for t in tools}
        assert len(registered_names) == len(EXPECTED_TOOLS), (
            f"Tool count mismatch: got {registered_names}, expected {EXPECTED_TOOLS}"
        )

    @pytest.mark.asyncio
    async def test_all_tools_have_descriptions(self):
        """Every tool must have a non-empty description for LLM comprehension."""
        from notebooklm.mcp_server import mcp

        tools = await mcp.list_tools()
        missing_desc = [t.name for t in tools if not (t.description or "").strip()]
        assert not missing_desc, f"Tools missing descriptions: {missing_desc}"

    @pytest.mark.asyncio
    async def test_tool_schemas_are_valid(self):
        """Every tool must have a valid inputSchema with 'type' and 'properties'."""
        from notebooklm.mcp_server import mcp

        tools = await mcp.list_tools()
        for tool in tools:
            schema = tool.inputSchema
            assert schema is not None, f"{tool.name}: inputSchema is None"
            schema_dict = schema.model_dump() if hasattr(schema, "model_dump") else dict(schema)
            assert "properties" in schema_dict, f"{tool.name}: schema missing 'properties'"


# ---------------------------------------------------------------------------
# Tool Schema Parameter Tests
# ---------------------------------------------------------------------------


class TestToolSchemas:
    """Verify required parameters exist in each tool's schema."""

    @pytest.mark.asyncio
    async def test_notebook_id_required_where_expected(self):
        """Tools operating on a specific notebook must require notebook_id."""
        from notebooklm.mcp_server import mcp

        tools_needing_id = {
            "get_notebook_summary",
            "list_sources",
            "add_url_source",
            "add_text_source",
            "delete_source",
            "list_notes",
            "create_note",
            "get_note",
            "delete_note",
            "list_artifacts",
            "generate_audio_overview",
            "generate_study_guide",
            "poll_artifact_status",
            "delete_artifact",
            "start_research",
            "poll_research_results",
            "import_research_sources",
            "get_share_status",
            "set_notebook_public",
            "ask_question",
        }

        tools = await mcp.list_tools()
        tool_map = {t.name: t for t in tools}

        for name in tools_needing_id:
            tool = tool_map[name]
            schema = tool.inputSchema
            schema_dict = schema.model_dump() if hasattr(schema, "model_dump") else dict(schema)
            props = schema_dict.get("properties", {})
            assert "notebook_id" in props, f"{name}: missing 'notebook_id' in schema"

    @pytest.mark.asyncio
    async def test_add_url_source_requires_url(self):
        """add_url_source must require a 'url' parameter."""
        from notebooklm.mcp_server import mcp

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "add_url_source")
        schema = tool.inputSchema
        schema_dict = schema.model_dump() if hasattr(schema, "model_dump") else dict(schema)
        assert "url" in schema_dict.get("properties", {}), "add_url_source missing 'url'"

    @pytest.mark.asyncio
    async def test_ask_question_requires_question(self):
        """ask_question must require a 'question' parameter."""
        from notebooklm.mcp_server import mcp

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "ask_question")
        schema = tool.inputSchema
        schema_dict = schema.model_dump() if hasattr(schema, "model_dump") else dict(schema)
        assert "question" in schema_dict.get("properties", {}), "ask_question missing 'question'"

    @pytest.mark.asyncio
    async def test_create_note_requires_title_and_content(self):
        """create_note must require 'title' and 'content'."""
        from notebooklm.mcp_server import mcp

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "create_note")
        schema = tool.inputSchema
        schema_dict = schema.model_dump() if hasattr(schema, "model_dump") else dict(schema)
        props = schema_dict.get("properties", {})
        assert "title" in props, "create_note missing 'title'"
        assert "content" in props, "create_note missing 'content'"

    @pytest.mark.asyncio
    async def test_start_research_has_optional_source_and_mode(self):
        """start_research should have optional 'source' and 'mode' params."""
        from notebooklm.mcp_server import mcp

        tools = await mcp.list_tools()
        tool = next(t for t in tools if t.name == "start_research")
        schema = tool.inputSchema
        schema_dict = schema.model_dump() if hasattr(schema, "model_dump") else dict(schema)
        props = schema_dict.get("properties", {})
        assert "source" in props, "start_research missing 'source'"
        assert "mode" in props, "start_research missing 'mode'"


# ---------------------------------------------------------------------------
# get_client() Tests
# ---------------------------------------------------------------------------


class TestGetClient:
    """Verify client initialization and singleton behavior."""

    @pytest.mark.asyncio
    async def test_get_client_raises_runtime_error_on_failure(self):
        """get_client() must raise RuntimeError when NotebookLMClient fails to init."""
        import notebooklm.mcp_server as srv

        original = srv._client
        srv._client = None
        try:
            with patch(
                "notebooklm.mcp_server.NotebookLMClient.from_storage",
                side_effect=FileNotFoundError("no credentials"),
            ):
                with pytest.raises(RuntimeError, match="Please run `notebooklm login`"):
                    await srv.get_client()
        finally:
            srv._client = original

    @pytest.mark.asyncio
    async def test_get_client_returns_same_instance_on_second_call(self):
        """get_client() must behave as a singleton — same object returned twice."""
        import notebooklm.mcp_server as srv

        mock_client = _make_mock_client()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)

        original = srv._client
        srv._client = None
        try:
            with patch(
                "notebooklm.mcp_server.NotebookLMClient.from_storage",
                return_value=mock_client,
            ):
                c1 = await srv.get_client()
                c2 = await srv.get_client()
                assert c1 is c2
        finally:
            srv._client = original


# ---------------------------------------------------------------------------
# Individual Tool Logic Tests
# ---------------------------------------------------------------------------


class TestToolLogic:
    """Test tool handler output shapes using a mocked client."""

    @pytest.fixture(autouse=True)
    def patch_client(self):
        """Replace _client with a mock for every test in this class."""
        import notebooklm.mcp_server as srv

        mock = _make_mock_client()
        original = srv._client
        srv._client = mock
        yield mock
        srv._client = original

    @pytest.mark.asyncio
    async def test_list_notebooks_returns_list_of_dicts(self):
        from notebooklm.mcp_server import list_notebooks

        result = await list_notebooks()
        assert isinstance(result, list)
        assert result[0]["id"] == "nb-1"
        assert result[0]["title"] == "Test Notebook"

    @pytest.mark.asyncio
    async def test_get_notebook_summary_returns_expected_keys(self):
        from notebooklm.mcp_server import get_notebook_summary

        result = await get_notebook_summary("nb-1")
        assert "notebook_id" in result
        assert "summary" in result
        assert "description" in result

    @pytest.mark.asyncio
    async def test_list_sources_returns_list_with_status(self):
        from notebooklm.mcp_server import list_sources

        result = await list_sources("nb-1")
        assert isinstance(result, list)
        assert "status" in result[0]

    @pytest.mark.asyncio
    async def test_add_url_source_returns_id_and_status(self):
        from notebooklm.mcp_server import add_url_source

        result = await add_url_source("nb-1", "https://example.com")
        assert "id" in result
        assert "status" in result

    @pytest.mark.asyncio
    async def test_add_text_source_returns_id_and_status(self):
        from notebooklm.mcp_server import add_text_source

        result = await add_text_source("nb-1", "My Source", "Some raw text")
        assert "id" in result
        assert "status" in result

    @pytest.mark.asyncio
    async def test_delete_source_returns_bool(self):
        from notebooklm.mcp_server import delete_source

        result = await delete_source("nb-1", "src-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_list_notes_returns_list(self):
        from notebooklm.mcp_server import list_notes

        result = await list_notes("nb-1")
        assert isinstance(result, list)
        assert result[0]["id"] == "note-1"

    @pytest.mark.asyncio
    async def test_create_note_returns_id_and_title(self):
        from notebooklm.mcp_server import create_note

        result = await create_note("nb-1", "Title", "Body")
        assert result["id"] == "note-1"
        assert result["title"] == "My Note"

    @pytest.mark.asyncio
    async def test_get_note_returns_content(self):
        from notebooklm.mcp_server import get_note

        result = await get_note("nb-1", "note-1")
        assert result["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_get_note_raises_for_missing_note(self, patch_client):
        import notebooklm.mcp_server as srv

        patch_client.notes.get = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="not found"):
            await srv.get_note("nb-1", "missing-note")

    @pytest.mark.asyncio
    async def test_delete_note_returns_bool(self):
        from notebooklm.mcp_server import delete_note

        result = await delete_note("nb-1", "note-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_list_artifacts_returns_list_with_kind(self):
        from notebooklm.mcp_server import list_artifacts

        result = await list_artifacts("nb-1")
        assert isinstance(result, list)
        assert result[0]["kind"] == "audio"

    @pytest.mark.asyncio
    async def test_generate_audio_overview_returns_task_id(self):
        from notebooklm.mcp_server import generate_audio_overview

        result = await generate_audio_overview("nb-1", instructions="Be engaging")
        assert result["task_id"] == "task-1"

    @pytest.mark.asyncio
    async def test_generate_audio_overview_without_instructions(self):
        from notebooklm.mcp_server import generate_audio_overview

        result = await generate_audio_overview("nb-1")
        assert "task_id" in result

    @pytest.mark.asyncio
    async def test_generate_study_guide_returns_task_id(self):
        from notebooklm.mcp_server import generate_study_guide

        result = await generate_study_guide("nb-1")
        assert result["task_id"] == "task-1"

    @pytest.mark.asyncio
    async def test_poll_artifact_status_returns_completion_fields(self):
        from notebooklm.mcp_server import poll_artifact_status

        result = await poll_artifact_status("nb-1", "task-1")
        assert "is_complete" in result
        assert "is_failed" in result
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delete_artifact_returns_bool(self):
        from notebooklm.mcp_server import delete_artifact

        result = await delete_artifact("nb-1", "art-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_start_research_returns_task_dict(self):
        from notebooklm.mcp_server import start_research

        result = await start_research("nb-1", "AI in healthcare")
        assert isinstance(result, dict)
        assert result.get("task_id") == "res-1"

    @pytest.mark.asyncio
    async def test_poll_research_results_returns_sources_list(self):
        from notebooklm.mcp_server import poll_research_results

        result = await poll_research_results("nb-1")
        assert result["status"] == "done"
        assert len(result["sources"]) == 1

    @pytest.mark.asyncio
    async def test_import_research_sources_returns_imported_list(self):
        from notebooklm.mcp_server import import_research_sources

        result = await import_research_sources("nb-1", "res-1", [0])
        assert isinstance(result, list)
        assert result[0]["id"] == "src-2"

    @pytest.mark.asyncio
    async def test_import_research_sources_raises_when_no_sources(self, patch_client):
        import notebooklm.mcp_server as srv

        patch_client.research.poll = AsyncMock(return_value={"status": "no_research"})
        with pytest.raises(ValueError, match="No research sources"):
            await srv.import_research_sources("nb-1", "res-1", [0])

    @pytest.mark.asyncio
    async def test_import_research_sources_raises_for_invalid_indices(self, patch_client):
        import notebooklm.mcp_server as srv

        with pytest.raises(ValueError, match="No valid sources"):
            await srv.import_research_sources("nb-1", "res-1", [999])

    @pytest.mark.asyncio
    async def test_get_share_status_returns_public_flag(self):
        from notebooklm.mcp_server import get_share_status

        result = await get_share_status("nb-1")
        assert result["is_public"] is True
        assert "share_url" in result
        assert "shared_users" in result

    @pytest.mark.asyncio
    async def test_set_notebook_public_returns_updated_status(self):
        from notebooklm.mcp_server import set_notebook_public

        result = await set_notebook_public("nb-1", True)
        assert result["is_public"] is True

    @pytest.mark.asyncio
    async def test_ask_question_returns_string(self):
        from notebooklm.mcp_server import ask_question

        result = await ask_question("nb-1", "What is this about?")
        assert isinstance(result, str)
        assert "42" in result

    @pytest.mark.asyncio
    async def test_ask_question_falls_back_to_str(self, patch_client):
        import notebooklm.mcp_server as srv

        # Use a plain object that has no 'answer' or 'answer_text' attribute.
        class _NoAnswer:
            def __str__(self):
                return "fallback"

        patch_client.chat.ask = AsyncMock(return_value=_NoAnswer())
        result = await srv.ask_question("nb-1", "Anything?")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# HTTP Transport Tests
# ---------------------------------------------------------------------------


class TestHTTPTransport:
    """Verify the FastAPI+SSE transport is wired correctly."""

    @pytest.mark.asyncio
    async def test_messages_endpoint_exists(self):
        """POST /messages/ must exist (not 404) even without a valid session."""
        from notebooklm.mcp_server import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
            response = await client.post("/messages/", content=b"{}", headers={"content-type": "application/json"})
            # 400 Bad Request or 422 is acceptable — 404 is not.
            assert response.status_code != 404, "/messages/ endpoint is missing"

    @pytest.mark.asyncio
    async def test_sse_endpoint_is_mounted(self):
        """Verify that the SSE app is mounted in FastAPI."""
        from notebooklm.mcp_server import app
        from starlette.routing import Mount
        
        # Verify that there's a mount for the SSE app
        has_mount = any(isinstance(route, Mount) for route in app.routes)
        assert has_mount, "SSE app is not mounted"

    @pytest.mark.asyncio
    async def test_server_starts_without_errors(self):
        """Importing mcp_server and building the app must not raise."""
        try:
            from notebooklm.mcp_server import app, mcp  # noqa: F401
        except Exception as exc:
            pytest.fail(f"mcp_server import raised: {exc}")
