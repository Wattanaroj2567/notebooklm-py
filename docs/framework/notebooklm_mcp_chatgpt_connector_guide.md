# NotebookLM MCP ChatGPT Connector Guide

Status: active
Audience: ChatGPT users connected to this NotebookLM MCP server

## What This Connector Is For

This MCP connector lets ChatGPT operate NotebookLM through tools:

- create or reuse notebooks
- add sources
- run NotebookLM research
- ask grounded questions
- generate artifacts
- poll long-running jobs
- retrieve artifact content
- safely clean up notebooks, sources, and notes

## How ChatGPT Should Use It

For role and workflow guidance:

```text
ask_framework_manual
```

For new research:

```text
run_deep_search_workflow
follow next_action
```

For existing notebook RAG:

```text
list_sources
ask_question
```

For studio artifacts:

```text
generate_*
poll_artifact_status
get_artifact_content
```

For cleanup:

```text
delete_*(dry_run=true)
ask user to approve the target list
delete_*(dry_run=false, confirm=true, verify=true)
```

## What Users Should Tell ChatGPT

Good prompts:

- "Use my NotebookLM MCP connector. Create or reuse a notebook about this topic, add sources, and summarize in Thai."
- "Use the Framework Notebook first and follow the MCP playbook."
- "Generate a data table, poll until complete, then retrieve it as JSON."
- "Dry-run cleanup for duplicate notebooks and show me what would be deleted before deleting."

Avoid vague prompts like:

- "Delete old stuff."
- "Use whatever notebook."
- "Make a report somehow."

## Environment Requirement

The MCP server should be started with:

```bash
NOTEBOOKLM_FRAMEWORK_NOTEBOOK_ID=<framework-notebook-id>
```

Without this variable, `ask_framework_manual` will return a structured configuration error.

## Expected Agent Behavior

ChatGPT should:

- use explicit notebook IDs
- follow `next_action`
- wait for source readiness
- use `content_format` instead of guessing artifact format
- ask before destructive actions
- answer in Thai when the user writes in Thai unless requested otherwise

