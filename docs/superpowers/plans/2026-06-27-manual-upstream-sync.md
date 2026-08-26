# Manual Upstream Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manually dispatched GitHub Actions workflow that merges upstream into `main`, prepares a uniquely named integration branch, and hands a maintainer a link for opening a reviewed PR into `mcp`.

**Architecture:** Implement the workflow on a dedicated branch based on `origin/main`, not on the current `mcp` working tree. The workflow uses the GitHub REST API through the runner's authenticated `gh` CLI, writes only `main` and a run-specific sync branch, and never updates `mcp` directly. Contract tests parse the workflow and assert the trigger, permissions, fixed refs, API operations, and maintainer handoff.

**Tech Stack:** GitHub Actions YAML, Bash, GitHub CLI (`gh api`), PyYAML, pytest, Ruff, uv

---

## File Structure

- Create: `.github/workflows/sync-upstream.yml`
  - Owns the manual GitHub Actions orchestration and GitHub API calls.
- Create: `tests/unit/test_sync_upstream_workflow.py`
  - Owns static contract tests for trigger safety, permissions, branch mutation
    boundaries, and PR handoff.
- Reference only: `docs/superpowers/specs/2026-06-27-manual-upstream-sync-design.md`
  - Approved behavior and rollout design; do not modify during implementation.

The implementation branch must contain only the workflow and its focused test.
The design and plan remain on `mcp`; they must not be brought into `main` by
merging the entire `mcp` branch.

### Task 1: Create an isolated implementation branch from `origin/main`

**Files:**
- No repository file changes

- [ ] **Step 1: Confirm the current checkout and remote refs**

Run:

```powershell
git status --short --branch
git rev-parse origin/main
git rev-parse upstream/main
```

Expected:

- The current checkout remains on `mcp`.
- `host_login.ps1` and `notebook.bat` remain untracked and untouched.
- Both remote refs resolve to commit SHAs.

- [ ] **Step 2: Use the worktree skill before creating isolation**

Invoke `superpowers:using-git-worktrees`. Follow its directory-selection and
ignore-safety checks. Create the isolated branch:

```powershell
git worktree add .worktrees/manual-upstream-sync -b codex/manual-upstream-sync origin/main
```

Expected: a new worktree on `codex/manual-upstream-sync`, based exactly on
`origin/main`. If `.worktrees` is not ignored, stop and follow the skill's
ignore-safety instructions before creating it.

- [ ] **Step 3: Install the locked contributor environment**

From the new worktree, run:

```powershell
uv sync --frozen --extra browser --extra dev --extra markdown
```

Expected: exit code 0 with the locked environment installed.

- [ ] **Step 4: Verify the relevant baseline tests**

Run:

```powershell
uv run pytest tests/unit/test_ci_audit_scripts.py tests/unit/test_check_action_pinning.py tests/unit/test_check_workflow_secret_gates.py -q
```

Expected: all selected baseline tests pass. If they fail before any
implementation change, stop and report the baseline failure.

### Task 2: Add failing workflow contract tests

**Files:**
- Create: `tests/unit/test_sync_upstream_workflow.py`
- Test: `tests/unit/test_sync_upstream_workflow.py`

- [ ] **Step 1: Create the contract test file**

Create `tests/unit/test_sync_upstream_workflow.py` with:

```python
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
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
uv run pytest tests/unit/test_sync_upstream_workflow.py -v
```

Expected: three failures caused by
`FileNotFoundError: .github/workflows/sync-upstream.yml`. The failures must be
from the missing workflow, not an import or syntax error.

### Task 3: Implement the manual upstream sync workflow

**Files:**
- Create: `.github/workflows/sync-upstream.yml`
- Test: `tests/unit/test_sync_upstream_workflow.py`

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/sync-upstream.yml` with:

```yaml
name: Sync Upstream

on:
  workflow_dispatch: {}

permissions:
  contents: read

concurrency:
  group: sync-upstream
  cancel-in-progress: false

jobs:
  sync:
    name: Update main and prepare MCP sync
    runs-on: ubuntu-latest
    permissions:
      contents: write
    env:
      GH_TOKEN: ${{ github.token }}
      UPSTREAM_HEAD: teng-lin:main
      MAIN_BRANCH: main
      MCP_BRANCH: mcp
      SYNC_BRANCH: sync/upstream-main-to-mcp-${{ github.run_id }}
    steps:
      - name: Merge upstream into main
        id: sync_main
        shell: bash
        run: |
          set -euo pipefail

          response="${RUNNER_TEMP}/main-merge.json"
          if ! gh api \
            --method POST \
            --header "Accept: application/vnd.github+json" \
            --header "X-GitHub-Api-Version: 2022-11-28" \
            "repos/${GITHUB_REPOSITORY}/merges" \
            --field "base=${MAIN_BRANCH}" \
            --field "head=${UPSTREAM_HEAD}" \
            --field "commit_message=chore(sync): merge upstream main" \
            >"${response}"; then
            echo "::error::Unable to merge ${UPSTREAM_HEAD} into ${MAIN_BRANCH}."
            exit 1
          fi

          if [[ -s "${response}" ]]; then
            result="merged"
          else
            result="already-current"
          fi

          main_sha="$(
            gh api \
              --header "Accept: application/vnd.github+json" \
              --header "X-GitHub-Api-Version: 2022-11-28" \
              "repos/${GITHUB_REPOSITORY}/git/ref/heads/${MAIN_BRANCH}" \
              --jq ".object.sha"
          )"
          echo "result=${result}" >>"${GITHUB_OUTPUT}"
          echo "main_sha=${main_sha}" >>"${GITHUB_OUTPUT}"

      - name: Create the run-specific sync branch
        shell: bash
        env:
          MAIN_SHA: ${{ steps.sync_main.outputs.main_sha }}
        run: |
          set -euo pipefail

          gh api \
            --method POST \
            --header "Accept: application/vnd.github+json" \
            --header "X-GitHub-Api-Version: 2022-11-28" \
            "repos/${GITHUB_REPOSITORY}/git/refs" \
            --field "ref=refs/heads/${SYNC_BRANCH}" \
            --field "sha=${MAIN_SHA}" \
            >/dev/null

      - name: Try to integrate MCP changes
        id: integrate_mcp
        shell: bash
        run: |
          set -euo pipefail

          response="${RUNNER_TEMP}/mcp-merge.json"
          error_log="${RUNNER_TEMP}/mcp-merge-error.log"
          if gh api \
            --method POST \
            --header "Accept: application/vnd.github+json" \
            --header "X-GitHub-Api-Version: 2022-11-28" \
            "repos/${GITHUB_REPOSITORY}/merges" \
            --field "base=${SYNC_BRANCH}" \
            --field "head=${MCP_BRANCH}" \
            --field "commit_message=chore(sync): integrate mcp with upstream main" \
            >"${response}" 2>"${error_log}"; then
            state="clean"
          elif grep -q "HTTP 409" "${error_log}"; then
            cat "${error_log}" >&2
            echo "::warning title=Manual conflict resolution required::The sync branch was preserved at updated main."
            state="conflict"
          else
            cat "${error_log}" >&2
            echo "::error::Unable to integrate ${MCP_BRANCH} into ${SYNC_BRANCH}."
            exit 1
          fi

          echo "state=${state}" >>"${GITHUB_OUTPUT}"

      - name: Write maintainer handoff
        shell: bash
        env:
          MAIN_RESULT: ${{ steps.sync_main.outputs.result }}
          MAIN_SHA: ${{ steps.sync_main.outputs.main_sha }}
          INTEGRATION_STATE: ${{ steps.integrate_mcp.outputs.state }}
        run: |
          set -euo pipefail

          compare_url="https://github.com/${GITHUB_REPOSITORY}/compare/${MCP_BRANCH}...${SYNC_BRANCH}?expand=1"
          {
            echo "## Manual upstream sync"
            echo
            echo "- Main result: \`${MAIN_RESULT}\`"
            echo "- Main SHA: \`${MAIN_SHA}\`"
            echo "- Sync branch: \`${SYNC_BRANCH}\`"
            echo "- MCP integration: \`${INTEGRATION_STATE}\`"
            echo
            echo "[Open the comparison and create the MCP pull request](${compare_url})"
            if [[ "${INTEGRATION_STATE}" == "conflict" ]]; then
              echo
              echo "The first sync needs manual conflict resolution:"
              echo
              echo '```bash'
              echo "git fetch origin"
              echo "git switch --track origin/${SYNC_BRANCH}"
              echo "git merge origin/${MCP_BRANCH}"
              echo "# Resolve conflicts, test, commit, then:"
              echo "git push origin ${SYNC_BRANCH}"
              echo '```'
            fi
          } >>"${GITHUB_STEP_SUMMARY}"

          echo "::notice title=Open MCP sync PR::${compare_url}"
```

- [ ] **Step 2: Run the focused contract tests and verify GREEN**

Run:

```powershell
uv run pytest tests/unit/test_sync_upstream_workflow.py -v
```

Expected: `3 passed`.

- [ ] **Step 3: Run Ruff on the new test**

Run:

```powershell
uv run ruff check tests/unit/test_sync_upstream_workflow.py
uv run ruff format --check tests/unit/test_sync_upstream_workflow.py
```

Expected: both commands exit 0.

- [ ] **Step 4: Run the repository workflow-policy tests**

Run:

```powershell
uv run pytest tests/unit/test_ci_audit_scripts.py tests/unit/test_check_action_pinning.py tests/unit/test_check_workflow_secret_gates.py -q
```

Expected: all selected tests pass. In particular:

- top-level `contents: read` satisfies the read-only permissions policy;
- job-level `contents: write` is scoped to the sync job;
- `github.token` does not introduce an unapproved secret;
- no third-party action pinning is required because the workflow uses no
  `uses:` steps.

- [ ] **Step 5: Inspect and commit the implementation**

Run:

```powershell
git diff --check
git status --short
git diff -- .github/workflows/sync-upstream.yml tests/unit/test_sync_upstream_workflow.py
git add .github/workflows/sync-upstream.yml tests/unit/test_sync_upstream_workflow.py
git commit -m "feat(ci): add manual upstream sync workflow"
```

Expected: one commit containing only the workflow and its contract test.

### Task 4: Run complete local verification

**Files:**
- Verify: `.github/workflows/sync-upstream.yml`
- Verify: `tests/unit/test_sync_upstream_workflow.py`

- [ ] **Step 1: Re-run the focused regression suite from the commit**

Run:

```powershell
uv run pytest tests/unit/test_sync_upstream_workflow.py tests/unit/test_ci_audit_scripts.py tests/unit/test_check_action_pinning.py tests/unit/test_check_workflow_secret_gates.py -q
```

Expected: all selected tests pass with zero failures.

- [ ] **Step 2: Run repository lint and formatting checks**

Run:

```powershell
uv run ruff check .
uv run ruff format --check .
```

Expected: both commands exit 0.

- [ ] **Step 3: Run pre-commit checks**

Run:

```powershell
uv run pre-commit run --all-files
```

Expected: every configured hook passes. If a hook modifies a file, inspect the
change, rerun the focused tests, and amend only when the modification belongs
to the two implementation files.

- [ ] **Step 4: Confirm branch scope**

Run:

```powershell
git status --short --branch
git diff --check origin/main...HEAD
git diff --stat origin/main...HEAD
git log --oneline origin/main..HEAD
```

Expected:

- the worktree is clean;
- the diff contains only `.github/workflows/sync-upstream.yml` and
  `tests/unit/test_sync_upstream_workflow.py`;
- the branch has one focused implementation commit.

### Task 5: Publish the focused PR into `main`

**Files:**
- No additional local file changes

- [ ] **Step 1: Push the implementation branch after user approval**

Run:

```powershell
git push -u origin codex/manual-upstream-sync
```

Expected: the branch is published without changing `main` or `mcp`.

- [ ] **Step 2: Open a pull request targeting `main`**

Use title:

```text
feat(ci): add manual upstream sync workflow
```

Use body:

```markdown
## Summary

- add a workflow-dispatch-only upstream sync
- merge `teng-lin:main` into fork `main` without force-pushing
- prepare a run-specific sync branch and maintainer-created PR handoff for `mcp`
- preserve conflicted branches for explicit manual resolution

## Verification

- `uv run pytest tests/unit/test_sync_upstream_workflow.py tests/unit/test_ci_audit_scripts.py tests/unit/test_check_action_pinning.py tests/unit/test_check_workflow_secret_gates.py -q`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run pre-commit run --all-files`
```

Expected: a focused PR from `codex/manual-upstream-sync` into `main`.

### Task 6: Perform the first manual sync after the workflow PR is merged

**Files:**
- No local file changes unless conflict resolution is required on the generated
  sync branch

- [ ] **Step 1: Dispatch the workflow manually**

After the workflow PR is merged into `main`, run:

```powershell
gh workflow run sync-upstream.yml --repo Wattanaroj2567/notebooklm-py --ref main
```

Expected: GitHub accepts one manual workflow dispatch.

- [ ] **Step 2: Watch the dispatched run**

Capture and watch the latest run:

```powershell
$runId = gh run list --repo Wattanaroj2567/notebooklm-py --workflow sync-upstream.yml --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch $runId --repo Wattanaroj2567/notebooklm-py --exit-status
```

Expected:

- `main` is merged with `teng-lin:main`, or reported already current;
- a branch whose name starts with `sync/upstream-main-to-mcp-` and ends with
  the run ID exists;
- the run summary contains a compare link;
- the MCP integration will likely report `conflict` on the first run because
  the branches have diverged substantially.

- [ ] **Step 3: Confirm the original dependency advisory is gone from `main`**

Dispatch the existing audit manually:

```powershell
gh workflow run dependency-audit.yml --repo Wattanaroj2567/notebooklm-py --ref main
```

Find and watch the new audit run:

```powershell
$auditRunId = gh run list --repo Wattanaroj2567/notebooklm-py --workflow dependency-audit.yml --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch $auditRunId --repo Wattanaroj2567/notebooklm-py --exit-status
```

Expected: `dependency-audit` succeeds with upstream's `vcrpy >=8.2.1` lock
update. If another advisory has appeared, report the new package and advisory
instead of weakening or disabling the audit.

- [ ] **Step 4: Hand the comparison to the maintainer**

Open the compare link from the sync workflow summary and create the PR into
`mcp`. If GitHub reports conflicts, resolve them on the generated sync branch
using:

```powershell
git fetch origin
$syncBranch = "sync/upstream-main-to-mcp-$runId"
git switch --track "origin/$syncBranch"
git merge origin/mcp
```

Resolve each conflict deliberately, then run the repository's full required
checks before committing and pushing the resolution. Do not choose all of
either side wholesale for `pyproject.toml`, `uv.lock`, MCP server code, or
workflow files.
