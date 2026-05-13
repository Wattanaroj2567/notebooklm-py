# NotebookLM MCP Operating Playbook

Status: active
Audience: AI agents using the NotebookLM MCP connector

## Core Rule

Agents should not infer formats, next steps, or safety requirements. MCP responses are designed to expose them explicitly through fields such as `next_action`, `content_format`, `ok`, `error`, `dry_run`, and `verified`.

## Framework Knowledge

When an agent needs role guidance, workflow SOPs, or instructions for using this MCP server correctly, it should call:

```text
ask_framework_manual(question, role="auto")
```

Use specific roles when helpful:

- `Minnie`: planning, intake, user goal clarification
- `Reas`: reasoning, synthesis, comparison
- `Vera`: verification, citations, source hygiene
- `Indy`: orchestration, research workflow, tool routing
- `Day`: writing, report polish, user-facing response
- `Chris`: implementation, debugging, technical delivery

## Research Workflow

For a new research notebook:

```text
run_deep_search_workflow
-> follow next_action.tool
-> research_wait_and_import
-> list_sources
-> ask_question or generate artifacts
```

For an existing notebook:

```text
start_research
-> poll_research_results or research_wait_and_import
-> import_research_sources only when importing a selected subset
```

Do not start duplicate research if the notebook already contains equivalent ready sources.

## Source Workflow

Before asking or generating:

```text
list_sources
if processing_count > 0: wait_source_ready
if error_count > 0: report partial source failure
if ready_count == count: proceed
```

For URLs and YouTube:

```text
add_url_source(wait=false)
-> wait_source_ready
```

Do not block a ChatGPT MCP request on long indexing unless the user explicitly asked to wait.

## Artifact Workflow

Every studio generation tool returns a task payload and a structured `next_action`.

```text
generate_data_table / generate_report / generate_quiz / ...
-> poll_artifact_status
-> if completed, follow next_action
-> get_artifact_content
```

For data tables, prefer:

```text
get_artifact_content(format="json")
```

For reports, quizzes, and flashcards, use `format="markdown"` unless downstream automation requires JSON.

## Strict JSON Workflow

Use strict JSON when the next step is programmatic:

```text
ask_question(response_format="json", strict_json=true, citations_mode="separate")
```

If the result has `ok=false`, do not silently parse the raw answer. Surface the structured error or retry with a narrower schema/prompt.

## Cleanup Workflow

Delete operations must follow:

```text
delete_*(dry_run=true)
-> show would_delete to user
-> only after approval: delete_*(dry_run=false, confirm=true, verify=true)
```

This applies to notebooks, sources, and notes.

