"""One chat session of the web app: a Claude Agent SDK client with the meal-planner tools.

Events streamed to the UI (JSON-serialisable dicts):
  {"type": "text", "text": str}         a chunk of the agent's answer
  {"type": "tool", "name": str}         the agent calls a tool (short name)
  {"type": "memory", "text": str}       the agent stored a memory ("Gemerkt: ...")
  {"type": "changed", "areas": [...]}   a write tool changed these UI areas
  {"type": "error", "message": str}
  {"type": "done"}                      ends every turn
"""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    Message,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
)

from meal_planner import db, prompts
from meal_planner.tools import SERVER_NAME, build_tools

DEFAULT_MODEL = "sonnet"  # alias, the CLI resolves it to the current Sonnet
WEB_TOOLS = ["WebSearch", "WebFetch"]
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"
KICKOFF = (
    "[App] A new session started. This message comes from the app, not from the user. Greet "
    "the user and open the right flow as described under Start."
)


class ChatSession:
    def __init__(self) -> None:
        self._client: ClaudeSDKClient | None = None
        self._events: list[dict[str, Any]] = []  # filled by the tools, also from worker threads
        self._lock = asyncio.Lock()  # one turn at a time
        self._unfinished = False  # a turn whose consumer stopped reading before its end
        self._streamed_id: str | None = None  # API message whose text arrived as stream events
        self._has_text = False  # the current turn has sent text already
        self._new_block = False  # the next text starts a new text block

    async def start(self) -> None:
        """Connect the SDK client; the system prompt carries the context from the database."""
        with db.session_scope() as session:
            system_prompt = prompts.build_system_prompt(session, date.today())
        tools = build_tools(self._events.append)
        options = ClaudeAgentOptions(
            model=os.getenv("MEAL_PLANNER_MODEL") or DEFAULT_MODEL,
            system_prompt=system_prompt,
            mcp_servers={SERVER_NAME: create_sdk_mcp_server(name=SERVER_NAME, tools=tools)},
            strict_mcp_config=True,
            tools=WEB_TOOLS,  # no file or shell tools: data only changes through our tools
            allowed_tools=[*WEB_TOOLS, *(TOOL_PREFIX + t.name for t in tools)],
            permission_mode="dontAsk",
            setting_sources=[],  # keep the user's Claude Code settings out of this agent
            include_partial_messages=True,
            env={"ENABLE_TOOL_SEARCH": "false"},  # few tools, so load them upfront
        )
        self._client = ClaudeSDKClient(options=options)
        await self._client.connect()

    def greet(self) -> AsyncIterator[dict[str, Any]]:
        """Let the agent open the session with the flow that fits the saved data."""
        return self._turn(KICKOFF)

    def send(self, text: str) -> AsyncIterator[dict[str, Any]]:
        return self._turn(text)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def _turn(self, prompt: str) -> AsyncIterator[dict[str, Any]]:
        async with self._lock:
            try:
                if self._client is None:
                    raise RuntimeError("The chat session is not started.")
                if self._unfinished:  # skip the rest of an abandoned turn
                    async for _ in self._client.receive_response():
                        pass
                self._unfinished = True
                self._has_text = False
                await self._client.query(prompt)
                async for message in self._client.receive_response():
                    for event in self._to_events(message):
                        yield event
                    while self._events:
                        yield self._events.pop(0)
                self._unfinished = False
            except Exception as error:  # noqa: BLE001 - shown in the chat instead of a dead stream
                yield {"type": "error", "message": str(error) or type(error).__name__}
            yield {"type": "done"}

    def _to_events(self, message: Message) -> list[dict[str, Any]]:
        if isinstance(message, StreamEvent):
            return self._stream_events(message.event)
        if isinstance(message, AssistantMessage):
            # Normally streamed already. Without stream events (e.g. a non-streaming retry), use
            # the whole blocks. Error messages are reported by the ResultMessage instead.
            if message.message_id == self._streamed_id or message.error:
                return []
            events = []
            for block in message.content:
                if isinstance(block, TextBlock):
                    events += self._text(block.text, new_block=True)
                elif isinstance(block, ToolUseBlock):
                    events.append(_tool_event(block.name))
            return events
        if isinstance(message, ResultMessage) and message.is_error:
            detail = message.result or "; ".join(message.errors or []) or message.subtype
            return [{"type": "error", "message": detail}]
        return []

    def _stream_events(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        kind = event.get("type")
        if kind == "message_start":
            self._streamed_id = event["message"]["id"]
        elif kind == "content_block_start":
            block = event["content_block"]
            if block["type"] == "tool_use":
                return [_tool_event(block["name"])]
            if block["type"] == "text":
                return self._text(block.get("text", ""), new_block=True)
        elif kind == "content_block_delta" and event["delta"]["type"] == "text_delta":
            return self._text(event["delta"]["text"])
        return []

    def _text(self, text: str, new_block: bool = False) -> list[dict[str, Any]]:
        """A text event. A new text block after earlier text of the turn starts a new paragraph."""
        self._new_block = self._new_block or new_block
        if not text:
            return []
        if self._new_block and self._has_text:
            text = "\n\n" + text
        self._new_block = False
        self._has_text = True
        return [{"type": "text", "text": text}]


def _tool_event(name: str) -> dict[str, Any]:
    return {"type": "tool", "name": name.removeprefix(TOOL_PREFIX)}
