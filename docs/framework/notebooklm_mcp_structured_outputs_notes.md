# NotebookLM MCP Structured Output Notes

Status: active
Purpose: local digest for structured output behavior when official docs are hard to ingest as web sources

## References

- OpenAI structured outputs guide: https://platform.openai.com/docs/guides/structured-outputs
- OpenAI function calling guide: https://platform.openai.com/docs/guides/function-calling/function-calling-with-structured-outputs
- OpenAI Agents SDK guide: https://platform.openai.com/docs/guides/agents-sdk/
- OpenAI Agents SDK guardrails: https://openai.github.io/openai-agents-python/guardrails/
- OpenAI Agents SDK tracing: https://openai.github.io/openai-agents-python/tracing/
- Anthropic tool use overview: https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/overview

## Practical Rules for This MCP Server

Use structured outputs when downstream code or another agent must parse the answer.

For NotebookLM RAG:

```text
ask_question(response_format="json", strict_json=true, citations_mode="separate")
```

For Framework Notebook guidance:

```text
ask_framework_manual(response_format="json", strict_json=true)
```

If strict parsing fails, the MCP tool returns an explicit `ok=false` result. Do not continue as if the raw answer is structured.

## Tool Output Design

MCP tool results should expose machine-readable fields:

- `ok`: whether the call succeeded when success can be ambiguous
- `error.code`: stable error code
- `error.message`: human-readable explanation
- `next_action`: next tool and args
- `content_format`: actual format of returned artifact content
- `dry_run`: whether a destructive operation was simulated
- `verified`: whether a post-action verification check ran

## Retry Policy

Retry strict JSON only when:

- the prompt can be made narrower
- the requested schema can be simplified
- the failure is format drift rather than factual uncertainty

Do not retry destructive tools automatically.

## Guardrail Policy

For agent workflows:

- input guardrails should prevent unsafe destructive requests
- output guardrails should catch unsupported claims and invalid schemas
- traces should include tool calls and status transitions, but must not include credentials or private cookies

