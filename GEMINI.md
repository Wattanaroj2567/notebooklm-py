# NotebookLM Project Instructions

As the Gemini CLI agent for this repository, you MUST follow these specialized rules and workflows when interacting with the NotebookLM CLI.

## Core Directives

- **Explicit IDs:** ALWAYS pass explicit notebook IDs using `-n <id>` or `--notebook <id>` for all commands. DO NOT rely on the global context set by `notebooklm use` to ensure parallel safety.
- **Structured Output:** ALWAYS use the `--json` flag when running commands to obtain machine-readable results.
- **Deduplication & Research Protection:** 
    - BEFORE adding a new source or starting research, ALWAYS run `notebooklm source list --json` to check if a similar source or the same URL already exists.
    - If a research task for the same query was recently performed, reuse the existing sources instead of starting a new one.
- **Autonomy Rules:**
    - **Run Automatically:** `list`, `status`, `auth check`, `source list`, `artifact list`, `ask` (without `--save-as-note`), `source add`.
    - **ASK BEFORE RUNNING:** `delete`, `generate`, `download`, `ask --save-as-note`.
- **Parallel Workflows:** For long-running tasks (generation, research, source waiting), use the **Subagent Pattern**:
    1. Start the operation with `--json` and `--no-wait` (if applicable).
    2. Capture the `artifact_id`, `task_id`, or `source_id`.
    3. Delegate the "wait and verify" task to a subagent (`generalist`).
    4. Provide the subagent with the specific IDs and expected timeout.

## Common Workflows

### 1. Source Processing
Before chatting or generating content, ensure all sources are `ready`.
- Use `notebooklm source wait <id> -n <notebook_id> --timeout 600` in a subagent.

### 2. Content Generation
- Use `notebooklm generate <type> --notebook <id> --json` to start.
- Delegate waiting to a subagent: `notebooklm artifact wait <artifact_id> -n <notebook_id> --timeout <seconds>`.
- **Timeouts:** Audio (1200s), Video (2700s), Quiz/Flashcards (900s).

## Error Handling & Retries

- **Rate Limits:** If a `generate` command fails with a rate limit error, wait 5-10 minutes before suggesting a retry.
- **Timeouts:** If a `wait` command times out (Exit Code 2), check the status with `artifact list` before deciding next steps.
- **Auth Issues:** If commands fail with auth errors, run `notebooklm auth check` and if needed, inform the user to run `notebooklm login`.

## Framework Notebook

- **Source of truth:** Use the NotebookLM Framework Notebook configured by `NOTEBOOKLM_FRAMEWORK_NOTEBOOK_ID`.
- **MCP usage:** Ask role and workflow questions through `ask_framework_manual`; do not depend on local framework files for new automation.
- **Subagents:** Treat role names as capabilities that can read from NotebookLM: Minnie for planning, Reas for synthesis, Vera for verification, Indy for orchestration, Day for writing, Chris for technical implementation.

## Naming & Style
- Follow the commit message style: `feat(cli): ...`, `fix(cli): ...`, `refactor(test): ...`.
- Adhere to the coding standards in `AGENTS.md`.
