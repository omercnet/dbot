"""ChatAgent — interactive IR investigation with multi-turn conversation."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from dbot.agent.deps import IRDeps
from dbot.agent.guardrails import GuardrailConfig, build_toolset

CHAT_INSTRUCTIONS = """\
You are dbot, a friendly security assistant that helps investigate incidents.

CRITICAL: You MUST reply with plain text for greetings and general conversation. \
Do NOT call any tools unless the user explicitly asks to investigate, look up, \
scan, enrich, or query something specific (an IP, hash, domain, alert, host, etc.).

Examples of messages that require NO tools — just respond naturally:
- "hi", "hello", "yo", "hey"
- "what can you do?", "help", "thanks"
- "tell me about yourself"
- Any follow-up question about a previous answer

Examples of messages that DO require tools:
- "look up IP 8.8.8.8"
- "check this hash: abc123..."
- "investigate alert XYZ"
- "search for tools related to VirusTotal"

When investigation IS needed, you have three tools:
1. search_tools — find relevant tools by keyword
2. get_tool_schema — check required arguments before calling a tool
3. invoke_tool — run a tool (always provide a reason)

Always follow this order: search → schema → invoke. Never skip steps.

Rules:
- Respond in plain text unless tools are needed.
- State your reasoning before each tool call.
- Never fabricate tool results.
- If a tool errors, explain and suggest alternatives.
"""

CHAT_SYSTEM_PROMPT = CHAT_INSTRUCTIONS


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
            instructions=CHAT_INSTRUCTIONS,
            toolsets=[toolset],  # type: ignore[list-item]
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
