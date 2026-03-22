"""ChatAgent — interactive IR investigation with multi-turn conversation."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from dbot.agent.deps import IRDeps
from dbot.agent.guardrails import GuardrailConfig, build_toolset

CHAT_SYSTEM_PROMPT = """\
You are dbot, an expert incident response analyst. You help security teams \
investigate alerts, enrich indicators, and assess threats.

You have access to 500+ security tool integrations via three tools:
1. search_tools — discover available tools by keyword or category
2. get_tool_schema — get argument specs before calling a tool
3. invoke_tool — execute a tool (you MUST provide a reason for audit)

Investigation workflow:
1. Understand the user's question or the alert being investigated.
2. Use search_tools to find relevant integrations.
3. Use get_tool_schema to understand required arguments.
4. Use invoke_tool to gather data. Always explain your reasoning.
5. Synthesize findings and present a clear assessment.

Rules:
- ALWAYS state your reasoning before calling a tool (the 'reason' argument).
- NEVER fabricate tool results — only report what tools actually return.
- If a tool returns an error, explain what happened and suggest alternatives.
- Ask for clarification if the request is ambiguous.
- Summarize each tool result before deciding next steps.
"""


class ChatAgent:
    """Interactive IR investigation agent with streaming and multi-turn history."""

    def __init__(
        self,
        config: GuardrailConfig | None = None,
        model: str | None = None,
    ) -> None:
        cfg = config or GuardrailConfig.chat_default()
        toolset = build_toolset(cfg)
        model_name = model or os.environ.get("DBOT_LLM_MODEL", "openai:gpt-4o")

        self._agent: Agent[IRDeps, str] = Agent(
            model_name,
            system_prompt=CHAT_SYSTEM_PROMPT,
            toolsets=[toolset],
            output_type=str,
            deps_type=IRDeps,
        )
        self._history: list[ModelMessage] = []

    async def send(self, message: str, deps: IRDeps) -> str:
        """Send a message and get a complete response."""
        result = await self._agent.run(
            message,
            deps=deps,
            message_history=self._history,
        )
        self._history = result.all_messages()
        return result.output

    async def send_stream(self, message: str, deps: IRDeps) -> AsyncIterator[str]:
        """Send a message and stream the response incrementally."""
        async with self._agent.run_stream(
            message,
            deps=deps,
            message_history=self._history,
        ) as stream:
            async for chunk in stream.stream_text(delta=True):
                yield chunk
            self._history = stream.all_messages()

    def reset(self) -> None:
        """Clear conversation history."""
        self._history = []

    @property
    def history(self) -> list[ModelMessage]:
        """Current conversation history."""
        return list(self._history)

    @property
    def agent(self) -> Agent[IRDeps, str]:
        """Access the underlying PydanticAI agent (for testing with .override())."""
        return self._agent
