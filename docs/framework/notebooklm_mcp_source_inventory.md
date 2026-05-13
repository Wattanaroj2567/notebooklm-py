# NotebookLM MCP Framework Source Inventory

Status: active
Purpose: source map for the Framework Notebook used by `ask_framework_manual`

## Source Tiers

### Tier 1: authoritative protocol and product docs

Use these when answering "how should MCP/NotebookLM behave?"

- Model Context Protocol specification: https://modelcontextprotocol.io/specification/2025-06-18/index
- MCP tools specification: https://modelcontextprotocol.io/specification/2025-06-18/server/tools
- MCP schema reference: https://modelcontextprotocol.io/specification/2025-06-18/schema
- MCP elicitation concept: https://modelcontextprotocol.io/docs/concepts/elicitation
- MCP authorization guidance: https://modelcontextprotocol.io/docs/tutorials/security/authorization
- MCP security best practices: https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices
- NotebookLM Help center: https://support.google.com/notebooklm
- Learn about NotebookLM: https://support.google.com/notebooklm/answer/16164461?hl=en
- Create a notebook in NotebookLM: https://support.google.com/notebooklm/answer/16206563?hl=en
- Add or discover sources in NotebookLM: https://support.google.com/notebooklm/answer/16215270?co=GENIE.Platform%3DDesktop&hl=en

### Tier 2: agent and structured-output guidance

Use these when designing tool outputs, strict JSON, handoffs, and guardrails.

- OpenAI function calling guide: https://platform.openai.com/docs/guides/function-calling/function-calling-with-structured-outputs
- OpenAI structured outputs guide: https://platform.openai.com/docs/guides/structured-outputs
- OpenAI Agents SDK guide: https://platform.openai.com/docs/guides/agents-sdk/
- OpenAI Agents SDK guardrails: https://openai.github.io/openai-agents-python/guardrails/
- OpenAI Agents SDK tracing: https://openai.github.io/openai-agents-python/tracing/
- Anthropic tool use overview: https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/overview

### Tier 3: security references

Use these when the workflow touches destructive actions, external URLs, credentials, public connectors, or untrusted retrieved content.

- OWASP MCP Top 10: https://owasp.org/www-project-mcp-top-10/
- OWASP MCP Tool Poisoning: https://owasp.org/www-community/attacks/MCP_Tool_Poisoning
- NIST AI Risk Management Framework: https://www.nist.gov/itl/ai-risk-management-framework

### Tier 4: local operating sources

These are project-specific and should override generic advice when they are more specific.

- NotebookLM MCP Operating Playbook
- NotebookLM MCP AI Team Roles
- NotebookLM MCP Safety Policy
- NotebookLM MCP ChatGPT Connector Guide

## Retrieval Rules

- Prefer Tier 1 for protocol and product behavior.
- Prefer Tier 4 for this repository's MCP tool order, response contracts, and safety workflow.
- Use Tier 2 for structured outputs, tool schemas, and agent orchestration design.
- Use Tier 3 when deciding whether an action needs user confirmation or extra validation.
- When sources conflict, choose the most specific source that is also authoritative for the question.

