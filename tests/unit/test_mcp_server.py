"""Unit tests for notebooklm MCP server.

Tests cover:
- Tool discovery: all expected tools registered with metadata
- Tool schema: input schemas expose expected properties
- Error propagation: RuntimeError when client init fails
- Client singleton: get_client() returns the same instance
- Server transport: /messages/ responds (not 404)
- Representative tool handlers with a mocked NotebookLM client
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from notebooklm.exceptions import ValidationError
from notebooklm.rpc.types import ShareAccess, SharePermission, ShareViewLevel, SourceStatus
from notebooklm.types import Artifact, SharedUser, ShareStatus

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Every @mcp.tool in notebooklm.mcp_server.
EXPECTED_TOOLS = frozenset(
    {
        "read_framework_manual",
        "run_research_team_workflow",
        "run_deep_search_workflow",
        "list_notebooks",
        "create_notebook",
        "delete_notebook",
        "get_notebook_summary",
        "list_sources",
        "add_url_source",
        "add_text_source",
        "refresh_source",
        "get_source_fulltext",
        "delete_source",
        "generate_audio_overview",
        "generate_video_overview",
        "generate_cinematic_video",
        "generate_report",
        "generate_quiz",
        "generate_flashcards",
        "generate_infographic",
        "generate_slide_deck",
        "generate_data_table",
        "generate_mind_map",
        "poll_artifact_status",
        "ask_question",
        "list_notes",
        "create_note",
        "export_artifact",
        "start_research",
        "poll_research_results",
        "import_research_sources",
        "research_wait_and_import",
        "rename_notebook",
        "get_share_status",
        "set_notebook_public",
        "list_artifacts",
        "get_artifact",
        "get_note",
        "rename_note",
        "delete_note",
        "wait_source_ready",
        "add_drive",
        "add_file",
    }
)

TOOLS_WITH_NOTEBOOK_ID = frozenset(
    {
        "delete_notebook",
        "get_notebook_summary",
        "list_sources",
        "add_url_source",
        "add_text_source",
        "refresh_source",
        "get_source_fulltext",
        "delete_source",
        "generate_audio_overview",
        "generate_video_overview",
        "generate_cinematic_video",
        "generate_report",
        "generate_quiz",
        "generate_flashcards",
        "generate_infographic",
        "generate_slide_deck",
        "generate_data_table",
        "generate_mind_map",
        "poll_artifact_status",
        "ask_question",
        "list_notes",
        "create_note",
        "export_artifact",
        "start_research",
        "poll_research_results",
        "import_research_sources",
        "research_wait_and_import",
        "rename_notebook",
        "get_share_status",
        "set_notebook_public",
        "list_artifacts",
        "get_artifact",
        "get_note",
        "rename_note",
        "delete_note",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client():
    """Build a NotebookLMClient mock covering all MCP tool code paths."""
    client = AsyncMock()

    nb = MagicMock(id="nb-1", title="Test Notebook")
    client.notebooks.list = AsyncMock(return_value=[nb])
    client.notebooks.create = AsyncMock(return_value=nb)
    client.notebooks.delete = AsyncMock(return_value=True)
    client.notebooks.rename = AsyncMock(
        side_effect=lambda notebook_id, new_title: MagicMock(
            id=notebook_id, title=new_title)
    )
    topic = MagicMock(question="Sample topic?")
    desc = MagicMock(summary="Notebook summary text", suggested_topics=[topic])
    client.notebooks.get_description = AsyncMock(return_value=desc)

    source = MagicMock(id="src-1", title="Source One",
                       url="https://example.com/page")
    source.status = SourceStatus.READY
    client.sources.list = AsyncMock(return_value=[source])
    client.sources.add_url = AsyncMock(return_value=source)
    client.sources.add_text = AsyncMock(return_value=source)
    client.sources.delete = AsyncMock(return_value=True)
    client.sources.refresh = AsyncMock(return_value=True)
    fulltext = MagicMock(content="full text body", char_count=42)
    client.sources.get_fulltext = AsyncMock(return_value=fulltext)
    client.sources.wait_until_ready = AsyncMock(return_value=None)

    note = MagicMock(id="note-1", title="My Note", content="Hello world")
    client.notes.list = AsyncMock(return_value=[note])
    client.notes.create = AsyncMock(return_value=note)
    client.notes.get = AsyncMock(return_value=note)
    client.notes.update = AsyncMock(return_value=None)
    client.notes.delete = AsyncMock(return_value=True)

    task_status = MagicMock(
        task_id="task-1", status="pending", is_complete=False)
    client.artifacts.generate_audio = AsyncMock(return_value=task_status)
    client.artifacts.generate_video = AsyncMock(return_value=task_status)
    client.artifacts.generate_cinematic_video = AsyncMock(
        return_value=task_status)
    client.artifacts.generate_report = AsyncMock(return_value=task_status)
    client.artifacts.generate_quiz = AsyncMock(return_value=task_status)
    client.artifacts.generate_flashcards = AsyncMock(return_value=task_status)
    client.artifacts.generate_infographic = AsyncMock(return_value=task_status)
    client.artifacts.generate_slide_deck = AsyncMock(return_value=task_status)
    client.artifacts.generate_data_table = AsyncMock(return_value=task_status)
    client.artifacts.generate_mind_map = AsyncMock(
        return_value={"note_id": "mm-1", "mind_map": {}})
    client.artifacts.poll_status = AsyncMock(return_value=task_status)
    client.artifacts.export = AsyncMock(
        return_value={"url": "https://docs.example/exported"})

    client.research.start = AsyncMock(
        return_value={"task_id": "res-1", "status": "running"})
    client.research.poll = AsyncMock(
        return_value={
            "task_id": "res-1",
            "status": "completed",
            "sources": [
                {
                    "url": "https://en.wikipedia.org",
                    "title": "Wiki",
                    "research_task_id": "res-1",
                },
                {
                    "url": "https://example.com/2",
                    "title": "Two",
                    "research_task_id": "res-1",
                },
            ],
        }
    )
    client.research.import_sources = AsyncMock(
        return_value=[{"id": "src-2", "title": "Wiki"}])

    share_status = ShareStatus(
        notebook_id="nb-1",
        is_public=False,
        access=ShareAccess.RESTRICTED,
        view_level=ShareViewLevel.FULL_NOTEBOOK,
        shared_users=[SharedUser(
            email="peer@example.com", permission=SharePermission.VIEWER)],
        share_url=None,
    )
    client.sharing.get_status = AsyncMock(return_value=share_status)
    client.sharing.set_public = AsyncMock(return_value=share_status)

    studio_art = Artifact(id="art-1", title="Studio A",
                          _artifact_type=1, status=3)
    client.artifacts.list = AsyncMock(return_value=[studio_art])
    client.artifacts.get = AsyncMock(
        side_effect=lambda _nid, aid: studio_art if aid == "art-1" else None
    )

    answer = MagicMock(answer="The answer is 42.")
    client.chat.ask = AsyncMock(return_value=answer)

    return client


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------


class TestToolDiscovery:
    @pytest.mark.asyncio
    async def test_all_expected_tools_are_registered(self):
        from notebooklm.mcp_server import mcp

        tools = await mcp.list_tools()
        registered_names = {t.name for t in tools}
        missing = EXPECTED_TOOLS - registered_names
        assert not missing, f"Missing tools: {missing}"

    @pytest.mark.asyncio
    async def test_no_extra_unexpected_tools(self):
        from notebooklm.mcp_server import mcp

        tools = await mcp.list_tools()
        registered_names = {t.name for t in tools}
        extra = registered_names - EXPECTED_TOOLS
        assert not extra, f"Unexpected tools: {extra}"

    @pytest.mark.asyncio
    async def test_all_tools_have_descriptions(self):
        from notebooklm.mcp_server import mcp

        tools = await mcp.list_tools()
        missing_desc = [t.name for t in tools if not (
            t.description or "").strip()]
        assert not missing_desc, f"Tools missing descriptions: {missing_desc}"

    @pytest.mark.asyncio
    async def test_tool_schemas_are_valid(self):
        from notebooklm.mcp_server import mcp

        tools = await mcp.list_tools()
        for tool in tools:
            schema = tool.inputSchema
            assert schema is not None, f"{tool.name}: inputSchema is None"
            schema_dict = schema.model_dump() if hasattr(
                schema, "model_dump") else dict(schema)
            assert "properties" in schema_dict, f"{tool.name}: schema missing 'properties'"


# ---------------------------------------------------------------------------
# Tool schema parameters
# ---------------------------------------------------------------------------


class TestToolSchemas:
    @pytest.mark.asyncio
    async def test_notebook_id_in_schema_where_expected(self):
        from notebooklm.mcp_server import mcp

        tools = await mcp.list_tools()
        tool_map = {t.name: t for t in tools}

        for name in TOOLS_WITH_NOTEBOOK_ID:
            tool = tool_map[name]
            schema_dict = (
                tool.inputSchema.model_dump()
                if hasattr(tool.inputSchema, "model_dump")
                else dict(tool.inputSchema)
            )
            props = schema_dict.get("properties", {})
            assert "notebook_id" in props, f"{name}: missing 'notebook_id' in schema"

    @pytest.mark.asyncio
    async def test_add_url_source_has_url_property(self):
        from notebooklm.mcp_server import mcp

        tool = next(t for t in await mcp.list_tools() if t.name == "add_url_source")
        schema_dict = (
            tool.inputSchema.model_dump()
            if hasattr(tool.inputSchema, "model_dump")
            else dict(tool.inputSchema)
        )
        assert "url" in schema_dict.get("properties", {})

    @pytest.mark.asyncio
    async def test_ask_question_has_question_property(self):
        from notebooklm.mcp_server import mcp

        tool = next(t for t in await mcp.list_tools() if t.name == "ask_question")
        schema_dict = (
            tool.inputSchema.model_dump()
            if hasattr(tool.inputSchema, "model_dump")
            else dict(tool.inputSchema)
        )
        assert "question" in schema_dict.get("properties", {})

    @pytest.mark.asyncio
    async def test_create_note_has_title_and_content(self):
        from notebooklm.mcp_server import mcp

        tool = next(t for t in await mcp.list_tools() if t.name == "create_note")
        schema_dict = (
            tool.inputSchema.model_dump()
            if hasattr(tool.inputSchema, "model_dump")
            else dict(tool.inputSchema)
        )
        props = schema_dict.get("properties", {})
        assert "title" in props and "content" in props

    @pytest.mark.asyncio
    async def test_run_deep_search_workflow_has_query_and_notebook_title(self):
        from notebooklm.mcp_server import mcp

        tool = next(t for t in await mcp.list_tools() if t.name == "run_deep_search_workflow")
        schema_dict = (
            tool.inputSchema.model_dump()
            if hasattr(tool.inputSchema, "model_dump")
            else dict(tool.inputSchema)
        )
        props = schema_dict.get("properties", {})
        assert "query" in props and "notebook_title" in props

    @pytest.mark.asyncio
    async def test_export_artifact_has_artifact_id(self):
        from notebooklm.mcp_server import mcp

        tool = next(t for t in await mcp.list_tools() if t.name == "export_artifact")
        schema_dict = (
            tool.inputSchema.model_dump()
            if hasattr(tool.inputSchema, "model_dump")
            else dict(tool.inputSchema)
        )
        assert "artifact_id" in schema_dict.get("properties", {})

    @pytest.mark.asyncio
    async def test_start_research_has_query(self):
        from notebooklm.mcp_server import mcp

        tool = next(t for t in await mcp.list_tools() if t.name == "start_research")
        props = (
            tool.inputSchema.model_dump()
            if hasattr(tool.inputSchema, "model_dump")
            else dict(tool.inputSchema)
        )["properties"]
        assert "query" in props

    @pytest.mark.asyncio
    async def test_import_research_sources_has_task_id(self):
        from notebooklm.mcp_server import mcp

        tool = next(t for t in await mcp.list_tools() if t.name == "import_research_sources")
        props = (
            tool.inputSchema.model_dump()
            if hasattr(tool.inputSchema, "model_dump")
            else dict(tool.inputSchema)
        )["properties"]
        assert "task_id" in props

    @pytest.mark.asyncio
    async def test_set_notebook_public_has_public_flag(self):
        from notebooklm.mcp_server import mcp

        tool = next(t for t in await mcp.list_tools() if t.name == "set_notebook_public")
        props = (
            tool.inputSchema.model_dump()
            if hasattr(tool.inputSchema, "model_dump")
            else dict(tool.inputSchema)
        )["properties"]
        assert "public" in props

    @pytest.mark.asyncio
    async def test_get_artifact_has_artifact_id(self):
        from notebooklm.mcp_server import mcp

        tool = next(t for t in await mcp.list_tools() if t.name == "get_artifact")
        props = (
            tool.inputSchema.model_dump()
            if hasattr(tool.inputSchema, "model_dump")
            else dict(tool.inputSchema)
        )["properties"]
        assert "artifact_id" in props

    @pytest.mark.asyncio
    async def test_rename_note_has_new_title(self):
        from notebooklm.mcp_server import mcp

        tool = next(t for t in await mcp.list_tools() if t.name == "rename_note")
        props = (
            tool.inputSchema.model_dump()
            if hasattr(tool.inputSchema, "model_dump")
            else dict(tool.inputSchema)
        )["properties"]
        assert "new_title" in props


# ---------------------------------------------------------------------------
# get_client()
# ---------------------------------------------------------------------------


class TestGetClient:
    @pytest.mark.asyncio
    async def test_get_client_raises_runtime_error_on_failure(self):
        import notebooklm.mcp_server as srv

        original = srv._client
        srv._client = None
        try:
            with (
                patch(
                    "notebooklm.mcp_server.NotebookLMClient.from_storage",
                    side_effect=FileNotFoundError("no credentials"),
                ),
                pytest.raises(
                    RuntimeError, match="NotebookLM client not ready"),
            ):
                await srv.get_client()
        finally:
            srv._client = original

    @pytest.mark.asyncio
    async def test_get_client_returns_same_instance_on_second_call(self):
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
# Tool logic (mocked client)
# ---------------------------------------------------------------------------


class TestToolLogic:
    @pytest.fixture(autouse=True)
    def patch_client(self):
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
        assert result["notebook_id"] == "nb-1"
        assert result["summary"] == "Notebook summary text"
        assert isinstance(result["topics"], list)
        assert result["topics"][0]["question"] == "Sample topic?"

    @pytest.mark.asyncio
    async def test_list_sources_returns_status_string(self):
        from notebooklm.mcp_server import list_sources

        result = await list_sources("nb-1")
        assert result[0]["status"] == "ready"

    @pytest.mark.asyncio
    async def test_add_url_source_returns_id_and_status(self):
        from notebooklm.mcp_server import add_url_source

        result = await add_url_source("nb-1", "https://example.com/new")
        assert "id" in result
        assert "status" in result

    @pytest.mark.asyncio
    async def test_add_text_source_returns_id_and_status(self):
        from notebooklm.mcp_server import add_text_source

        result = await add_text_source("nb-1", "My Source", "Some raw text")
        assert result["id"] == "src-1"

    @pytest.mark.asyncio
    async def test_poll_artifact_status_returns_status_and_complete_flag(self):
        from notebooklm.mcp_server import poll_artifact_status

        result = await poll_artifact_status("nb-1", "task-1")
        assert result == {"status": "pending", "is_complete": False}

    @pytest.mark.asyncio
    async def test_generate_report_returns_task_fields(self):
        from notebooklm.mcp_server import generate_report

        result = await generate_report("nb-1", format="STUDY_GUIDE")
        assert result["task_id"] == "task-1"
        assert "status" in result

    @pytest.mark.asyncio
    async def test_run_deep_search_workflow_completes(self, patch_client):
        from notebooklm.mcp_server import run_deep_search_workflow

        with patch("notebooklm.mcp_server.asyncio.sleep", new_callable=AsyncMock):
            result = await run_deep_search_workflow("climate tips", "Research NB")
        assert result["notebook_id"] == "nb-1"
        assert result["sources_imported"] == 1
        assert "summary" in result
        patch_client.research.start.assert_awaited()
        patch_client.research.import_sources.assert_awaited()

    @pytest.mark.asyncio
    async def test_start_research_returns_task(self):
        from notebooklm.mcp_server import start_research

        result = await start_research("nb-1", "q", source="web", mode="fast")
        assert result.get("task_id") == "res-1"

    @pytest.mark.asyncio
    async def test_start_research_validation_error_dict(self, patch_client):
        from notebooklm.mcp_server import start_research

        patch_client.research.start = AsyncMock(
            side_effect=ValidationError("bad combo"))
        result = await start_research("nb-1", "q", source="drive", mode="deep")
        assert result.get("error") == "bad combo"

    @pytest.mark.asyncio
    async def test_poll_research_results_returns_dict(self):
        from notebooklm.mcp_server import poll_research_results

        result = await poll_research_results("nb-1")
        assert result["status"] == "completed"
        assert len(result["sources"]) == 2

    @pytest.mark.asyncio
    async def test_import_research_sources_imports_all_by_default(self, patch_client):
        from notebooklm.mcp_server import import_research_sources

        out = await import_research_sources("nb-1", "res-1")
        assert isinstance(out, list)
        patch_client.research.import_sources.assert_awaited_once()
        args, kwargs = patch_client.research.import_sources.call_args
        assert args[0] == "nb-1" and args[1] == "res-1"
        assert len(args[2]) == 2

    @pytest.mark.asyncio
    async def test_import_research_sources_respects_indices(self, patch_client):
        from notebooklm.mcp_server import import_research_sources

        await import_research_sources("nb-1", "res-1", source_indices=[1])
        _args, _kwargs = patch_client.research.import_sources.call_args
        assert len(_args[2]) == 1
        assert _args[2][0]["title"] == "Two"

    @pytest.mark.asyncio
    async def test_import_research_sources_task_mismatch(self, patch_client):
        from notebooklm.mcp_server import import_research_sources

        out = await import_research_sources("nb-1", "wrong-id")
        assert isinstance(out, dict) and "error" in out

    @pytest.mark.asyncio
    async def test_research_wait_and_import(self, patch_client):
        from notebooklm.mcp_server import research_wait_and_import

        calls = {"n": 0}

        async def poll_side_effect(_nid: str):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"task_id": "res-1", "status": "in_progress", "sources": []}
            return {
                "task_id": "res-1",
                "status": "completed",
                "sources": [
                    {
                        "url": "https://a.test",
                        "title": "A",
                        "research_task_id": "res-1",
                    },
                ],
            }

        patch_client.research.poll = AsyncMock(side_effect=poll_side_effect)
        with patch("notebooklm.mcp_server.asyncio.sleep", new_callable=AsyncMock):
            result = await research_wait_and_import("nb-1", "res-1", timeout_seconds=60)
        assert result.get("status") == "imported"
        assert result.get("sources_imported") == 1

    @pytest.mark.asyncio
    async def test_rename_notebook(self):
        from notebooklm.mcp_server import rename_notebook

        result = await rename_notebook("nb-1", "New Title")
        assert result == {"id": "nb-1", "title": "New Title"}

    @pytest.mark.asyncio
    async def test_get_share_status_shape(self):
        from notebooklm.mcp_server import get_share_status

        result = await get_share_status("nb-1")
        assert result["notebook_id"] == "nb-1"
        assert result["is_public"] is False
        assert len(result["shared_users"]) == 1

    @pytest.mark.asyncio
    async def test_list_artifacts_returns_kinds(self):
        from notebooklm.mcp_server import list_artifacts

        rows = await list_artifacts("nb-1")
        assert len(rows) == 1
        assert rows[0]["id"] == "art-1"
        assert rows[0]["kind"] == "audio"

    @pytest.mark.asyncio
    async def test_get_artifact_found(self):
        from notebooklm.mcp_server import get_artifact

        row = await get_artifact("nb-1", "art-1")
        assert row["id"] == "art-1"

    @pytest.mark.asyncio
    async def test_get_artifact_not_found(self):
        from notebooklm.mcp_server import get_artifact

        row = await get_artifact("nb-1", "missing")
        assert row.get("error") == "Artifact not found"

    @pytest.mark.asyncio
    async def test_get_note_returns_content(self):
        from notebooklm.mcp_server import get_note

        row = await get_note("nb-1", "note-1")
        assert row["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_rename_note_calls_update(self, patch_client):
        from notebooklm.mcp_server import rename_note

        result = await rename_note("nb-1", "note-1", "Renamed")
        assert result["title"] == "Renamed"
        patch_client.notes.update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_note(self):
        from notebooklm.mcp_server import delete_note

        assert await delete_note("nb-1", "note-1") is True

    @pytest.mark.asyncio
    async def test_read_framework_manual_reads_file(self, tmp_path, monkeypatch):
        import notebooklm.mcp_server as srv

        ws = tmp_path / "ai_workspace"
        ws.mkdir()
        (ws / "master_automation_manual.md").write_text("manual-body", encoding="utf-8")
        monkeypatch.setattr(srv, "AI_WORKSPACE_DIR", ws)

        assert await srv.read_framework_manual() == "manual-body"

    @pytest.mark.asyncio
    async def test_ask_question_returns_string(self):
        from notebooklm.mcp_server import ask_question

        result = await ask_question("nb-1", "What is this about?")
        assert isinstance(result, str)
        assert "42" in result

    @pytest.mark.asyncio
    async def test_ask_question_falls_back_to_str(self, patch_client):
        import notebooklm.mcp_server as srv

        class _NoAnswer:
            def __str__(self):
                return "fallback"

        patch_client.chat.ask = AsyncMock(return_value=_NoAnswer())
        result = await srv.ask_question("nb-1", "Anything?")
        assert result == "fallback"


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------


class TestHTTPTransport:
    @pytest.mark.asyncio
    async def test_messages_endpoint_exists(self):
        from notebooklm.mcp_server import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
            response = await client.post(
                "/messages/", content=b"{}", headers={"content-type": "application/json"}
            )
            assert response.status_code != 404, "/messages/ endpoint is missing"

    @pytest.mark.asyncio
    async def test_sse_endpoint_is_mounted(self):
        from starlette.routing import Route

        from notebooklm.mcp_server import app

        paths = {r.path for r in app.routes if isinstance(r, Route)}
        assert "/sse" in paths or "/sse/" in paths, "SSE app is not mounted"

    def test_server_imports_cleanly(self):
        from notebooklm.mcp_server import app, mcp  # noqa: F401

        assert app.title == "NotebookLM Framework MCP"
        assert mcp.name == "NotebookLM-Agent-Framework"
