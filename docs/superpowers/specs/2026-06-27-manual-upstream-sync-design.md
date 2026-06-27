# Manual Upstream Sync Design

**Status:** Approved design
**Date:** 2026-06-27
**Repository:** `Wattanaroj2567/notebooklm-py`

## Context

This fork uses two long-lived branches:

- `main` carries the upstream project plus the small amount of fork-only
  configuration needed to operate the fork.
- `mcp` carries the fork-specific MCP server work and should periodically
  receive compatible changes from `main`.

The previous `sync-upstream.yml` ran on a schedule and pushed directly to a
branch. It was removed from `mcp`. The replacement must be intentionally
started by a maintainer, update `main` from `teng-lin/notebooklm-py`, and
prepare a reviewed path for bringing those changes into `mcp`.

At design time, `origin/main` and `upstream/main` merge without content
conflicts. `origin/mcp` has diverged substantially from upstream, so the first
MCP sync is expected to need manual conflict resolution. Upstream already
contains the `vcrpy` security update that resolves the current
`dependency-audit` failure on `main`.

## Goals

- Run only through `workflow_dispatch`; do not schedule automatic syncs.
- Merge `teng-lin/notebooklm-py:main` into the fork's `main` without rewriting
  history.
- Never push directly to `mcp`.
- Create a uniquely named sync branch and provide a link for a maintainer to
  open a pull request into `mcp`.
- Let the maintainer-created pull request trigger the repository's normal CI.
- Preserve a usable sync branch when the MCP merge has conflicts so a
  maintainer can resolve them explicitly.
- Use the repository-scoped `GITHUB_TOKEN`; do not require a personal access
  token or a new secret.

## Non-goals

- Automatically resolve merge conflicts.
- Automatically merge the resulting pull request.
- Force-push or rebase either long-lived branch.
- Keep `mcp` continuously synchronized.
- Change the schedules or behavior of unrelated workflows.

## Workflow Placement and Trigger

The workflow file will be `.github/workflows/sync-upstream.yml` on the default
branch, `main`, because GitHub only exposes a branch's manual workflow once the
workflow exists on the default branch.

It will have:

- `workflow_dispatch` as its only trigger;
- a concurrency group that allows only one upstream sync at a time;
- `contents: write` at job scope;
- no `pull-requests` permission because the workflow does not create the pull
  request itself.

The implementation must reach `main` through a focused pull request based on
`origin/main`. It must not be delivered by merging all of `mcp` into `main`.

## Data Flow

### 1. Synchronize `main`

The workflow calls the GitHub merge API with:

- base: `main`
- head: `teng-lin:main`

The API creates a regular merge commit when upstream has new commits. It
returns an already-up-to-date result when there is nothing to merge. No
force-push is used.

If GitHub reports a conflict or rejects the write because of branch
protection or repository permissions, the workflow stops. It does not create
an MCP sync branch from a stale `main`.

### 2. Create a sync branch

After `main` is current, the workflow reads the updated `main` SHA and creates
a unique branch:

`sync/upstream-main-to-mcp-<run-id>`

The branch starts at updated `main`. A unique run ID prevents a later run from
overwriting an earlier branch that may still be under review.

### 3. Attempt MCP integration

The workflow attempts to merge `mcp` into the sync branch. This direction is
intentional: the sync branch contains updated `main`, then incorporates the
current MCP work. If the merge is clean, the branch contains both histories
and is ready for a pull request whose base is `mcp`.

If the merge conflicts, GitHub leaves the sync branch at updated `main`. The
workflow treats this as an expected manual-resolution state, emits a warning,
and keeps the branch. It must not invent conflict resolutions or alter
`mcp`.

### 4. Handoff to the maintainer

The job summary displays:

- whether `main` changed or was already current;
- the updated `main` SHA;
- the sync branch name;
- whether the MCP integration was clean or needs manual resolution;
- a compare URL with `mcp` as the base and the sync branch as the head.

The maintainer opens the pull request from that URL. Because the PR is created
by the maintainer rather than by `GITHUB_TOKEN`, the normal `pull_request`
workflows run without GitHub's recursive-workflow suppression.

For a conflicted branch, the summary also explains that the maintainer must
merge `mcp` into the sync branch locally, resolve conflicts, and push the
resolved branch before the PR can be merged.

## Failure Handling

- **Upstream is already current:** continue and prepare an MCP sync branch.
- **`main` merge conflict:** fail immediately; do not create a sync branch.
- **Permission or branch-protection rejection:** fail with the GitHub API
  message and leave both long-lived branches unchanged.
- **Sync branch name collision:** fail instead of overwriting a branch. The
  run ID should make this exceptional.
- **`mcp` integration conflict:** preserve the branch, report a warning, and
  provide manual-resolution instructions and the compare URL.
- **Unexpected API or network error:** fail without retrying mutations
  blindly. A maintainer may inspect state and rerun the workflow.

## Security Boundaries

- Use only `GITHUB_TOKEN` exposed through the workflow environment.
- Grant only job-scoped `contents: write`.
- Do not persist checkout credentials; the API-based design does not require a
  checkout to mutate refs.
- Accept no user-provided repository, owner, ref, or shell input.
- Use the fixed upstream identity `teng-lin:main`.
- Do not use third-party actions for branch or pull-request creation.

## Verification

Implementation verification will include:

1. Parse the workflow as YAML and confirm `workflow_dispatch` is its only
   trigger.
2. Run the repository's workflow-policy, action-pinning, and secret-gate unit
   tests.
3. Add a focused unit test that confirms:
   - no schedule exists;
   - permissions are limited to `contents: write`;
   - the fixed source is `teng-lin:main`;
   - the workflow never updates `mcp` directly;
   - the sync branch uses the GitHub run ID;
   - the summary contains a compare URL for a maintainer-created PR.
4. Run Ruff on changed Python test files, if any.
5. After the workflow is merged into `main`, perform one manual run and verify:
   - `main` contains the latest upstream commit;
   - a unique sync branch exists;
   - the summary links to a comparison against `mcp`;
   - opening the PR manually starts the normal CI workflows.

The next scheduled `dependency-audit` on updated `main` provides the final
remote confirmation that the upstream `vcrpy` fix resolved the original alert.

## Rollout

1. Implement and test the workflow on a dedicated branch based on
   `origin/main`.
2. Open a focused pull request into `main`.
3. Merge that PR after repository checks pass.
4. Run **Sync Upstream** manually once.
5. Open the generated comparison as a PR into `mcp`.
6. Resolve conflicts on the sync branch if required, then merge only after CI
   passes.
7. Delete the temporary sync branch after the PR is merged or abandoned.
