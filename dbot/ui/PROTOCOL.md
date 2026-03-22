# PydanticAI `/api/chat` Vercel AI Data Stream Protocol (dbot)

This document describes the exact wire protocol used by dbot’s PydanticAI web endpoint.

## Source of truth

- PydanticAI package path (hatch env):
  - `/home/omer/.local/share/hatch/env/virtual/dbot/zaM0UAM1/dbot/lib/python3.14/site-packages/pydantic_ai/__init__.py`
- Read files:
  - `pydantic_ai/ui/_web/app.py`
  - `pydantic_ai/ui/_web/api.py`
  - `pydantic_ai/ui/vercel_ai/request_types.py`
  - `pydantic_ai/ui/vercel_ai/response_types.py`
  - `pydantic_ai/ui/vercel_ai/_event_stream.py`
  - `pydantic_ai/ui/vercel_ai/_adapter.py`
  - `dbot/agent/guardrails.py`

## Endpoint + routing

- `create_web_app()` mounts API app at `/api`.
- `create_api_app()` defines route `POST /chat`.
- Effective chat URL: **`POST /api/chat`**.

## Request format (`SubmitMessage`)

`VercelAIAdapter.build_run_input()` validates request body as discriminated union `RequestData`.

For normal send-message flow:

```json
{
  "trigger": "submit-message",
  "id": "<request-id>",
  "messages": [
    {
      "id": "<message-id>",
      "role": "system|user|assistant",
      "metadata": {"...": "..."},
      "parts": [ /* UIMessagePart[] */ ]
    }
  ],
  "model": "openai:gpt-5",
  "builtinTools": ["web_search", "code_execution"]
}
```

Notes:

- `SubmitMessage` is `extra='allow'`, so extra top-level fields are accepted.
- dbot/PydanticAI uses extras `model` and `builtinTools` (`ChatRequestExtra`) to choose model/tools.
- `RegenerateMessage` (`trigger: regenerate-message`) is also supported by the adapter type union.

## Response format (SSE)

Streaming response is Starlette `StreamingResponse` with:

- `Content-Type: text/event-stream`
- `x-vercel-ai-ui-message-stream: v1`

Framing (`VercelAIEventStream.encode_event`):

```text
data: <chunk-json-or-[DONE]>

```

Terminator:

- `DoneChunk.encode()` returns literal `[DONE]`
- Therefore final frame is exactly:

```text
data: [DONE]

```

## Field naming on the wire

All chunk/request models inherit camelCase aliasing (`alias_generator=to_camel`) and serialize with `by_alias=True`.

- Example conversions:
  - `tool_call_id` -> `toolCallId`
  - `provider_metadata` -> `providerMetadata`
  - `error_text` -> `errorText`
  - `message_id` -> `messageId`
  - `finish_reason` -> `finishReason`

Null fields are omitted (`exclude_none=True`).

## All SSE chunk/event types (`response_types.py`)

- `text-start` `{ type, id, providerMetadata? }`
- `text-delta` `{ type, delta, id, providerMetadata? }`
- `text-end` `{ type, id, providerMetadata? }`
- `reasoning-start` `{ type, id, providerMetadata? }`
- `reasoning-delta` `{ type, id, delta, providerMetadata? }`
- `reasoning-end` `{ type, id, providerMetadata? }`
- `error` `{ type, errorText }`
- `tool-input-start` `{ type, toolCallId, toolName, providerExecuted?, providerMetadata?, dynamic? }`
- `tool-input-delta` `{ type, toolCallId, inputTextDelta }`
- `tool-input-available` `{ type, toolCallId, toolName, input, providerExecuted?, providerMetadata?, dynamic? }`
- `tool-input-error` `{ type, toolCallId, toolName, input, providerExecuted?, providerMetadata?, dynamic?, errorText }`
- `tool-output-available` `{ type, toolCallId, output, providerExecuted?, dynamic?, preliminary? }`
- `tool-output-error` `{ type, toolCallId, errorText, providerExecuted?, dynamic? }`
- `tool-approval-request` `{ type, approvalId, toolCallId }` (SDK v6 path)
- `tool-output-denied` `{ type, toolCallId }` (SDK v6 path)
- `source-url` `{ type, sourceId, url, title?, providerMetadata? }`
- `source-document` `{ type, sourceId, mediaType, title, filename?, providerMetadata? }`
- `file` `{ type, url, mediaType }`
- `data-*` `{ type: "data-...", id?, data, transient? }`
- `start-step` `{ type }`
- `finish-step` `{ type }`
- `start` `{ type, messageId?, messageMetadata? }`
- `finish` `{ type, finishReason?, messageMetadata? }`
- `abort` `{ type, reason? }`
- `message-metadata` `{ type, messageMetadata }`
- `done` -> encoded as `[DONE]`

## Tool event payload specifics

### `tool-input-available`

Emitted when a tool call is fully formed:

```json
{
  "type": "tool-input-available",
  "toolCallId": "...",
  "toolName": "invoke_tool",
  "input": {"tool_name":"...","args":{},"reason":"..."},
  "providerExecuted": false,
  "providerMetadata": {"pydantic_ai": {"id":"...","provider_name":"...","provider_details":{}}}
}
```

### `tool-output-available`

Emitted when tool returns successfully (or generally non-error branch):

```json
{
  "type": "tool-output-available",
  "toolCallId": "...",
  "output": {"...": "..."},
  "providerExecuted": false
}
```

`output` value is produced by `tool_return_output(part)`:

- `obj = part.model_response_object()`
- if `obj` has key `return_value`, stream `obj.return_value`
- else stream entire `obj`

So `invoke_tool` return dict is what appears in `output` in the common case.

## dbot `invoke_tool` output shapes (`dbot/agent/guardrails.py`)

Possible returned dictionaries (embedded in tool output):

1. Unknown tool:

```json
{"status":"error","tool_name":"...","error":"Tool '...' not found"}
```

2. Blocked by policy (`ToolCall.model_dump()`):

```json
{"tool_name":"...","args":{},"reason":"...","status":"blocked_by_policy", ...}
```

3. Dangerous tool requires approval:

```json
{"status":"approval_required","tool_name":"...","args":{},"reason":"...","description":"..."}
```

4. Missing credentials:

```json
{"status":"credentials_required","tool_name":"...","pack":"...","error":"...","required_credentials":["..."]}
```

5. Executed result:

```json
{
  "tool_name":"...",
  "reason":"...",
  "success": true,
  "results": [],
  "error": null,
  "duration_ms": 12.34
}
```

## `@ai-sdk/react` (`useChat`) compatibility assessment

### Protocol compatibility

- **Yes, compatible by protocol**:
  - SSE framing is correct (`data: ...\n\n`)
  - Required header is present (`x-vercel-ai-ui-message-stream: v1`)
  - Terminator is correct (`[DONE]`)
  - Event taxonomy matches Vercel AI UI data stream vocabulary.

### Request compatibility

- `useChat`/transport must send Vercel UI-message payload (`trigger`, `id`, `messages`), which matches `SubmitMessage`.
- dbot-specific extras (`model`, `builtinTools`) are optional and supported.

### Version notes

- PydanticAI `VercelAIAdapter` defaults `sdk_version=5` in this endpoint path.
- v6-only approval chunks exist in types, but are only emitted when adapter runs with `sdk_version>=6`.

### When adapter/custom config is needed

- If frontend uses **text stream protocol** instead of **data stream protocol**, set `streamProtocol: 'text'` client-side (not default path for this backend).
- If using stock `DefaultChatTransport`/`useChat` data stream mode, no protocol adapter is required for this backend.
