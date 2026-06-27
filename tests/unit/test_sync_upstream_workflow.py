from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "sync-upstream.yml"


def _load_workflow() -> tuple[dict[str, object], str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    data = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(data, dict)
    return data, text


def test_sync_upstream_is_manual_only_and_serialized():
    data, _ = _load_workflow()

    assert set(data["on"]) == {"workflow_dispatch"}
    assert data["concurrency"] == {
        "group": "sync-upstream",
        "cancel-in-progress": "false",
    }


def test_sync_upstream_uses_minimal_permissions_and_fixed_refs():
    data, _ = _load_workflow()
    job = data["jobs"]["sync"]

    assert data["permissions"] == {"contents": "read"}
    assert job["permissions"] == {"contents": "write"}
    assert job["env"]["UPSTREAM_HEAD"] == "teng-lin:main"
    assert job["env"]["MAIN_BRANCH"] == "main"
    assert job["env"]["MCP_BRANCH"] == "mcp"
    assert job["env"]["SYNC_BRANCH"] == ("sync/upstream-main-to-mcp-${{ github.run_id }}")


def test_sync_upstream_never_updates_mcp_and_hands_off_by_compare_url():
    _, text = _load_workflow()

    assert '--field "base=${MAIN_BRANCH}"' in text
    assert '--field "head=${UPSTREAM_HEAD}"' in text
    assert '--field "base=${SYNC_BRANCH}"' in text
    assert '--field "head=${MCP_BRANCH}"' in text
    assert "ref=refs/heads/${SYNC_BRANCH}" in text
    assert "ref=refs/heads/${MCP_BRANCH}" not in text
    assert "compare/${MCP_BRANCH}...${SYNC_BRANCH}?expand=1" in text
    assert "pull-requests: write" not in text
