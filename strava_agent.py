"""A LangGraph training assistant backed by the local Strava MCP server.

Prerequisite: `uv run python strava_mcp_server.py` running in a terminal.

Usage (in a notebook, or any async code):

    agent = StravaAgent()
    print(await agent.ask("Summarise my training over the last 2 weeks."))
    print(await agent.ask("Which of those was hardest?"))  # remembers context
    agent.reset()  # start a fresh conversation
"""

import asyncio
import uuid
from collections.abc import Sequence

import httpx
from langchain_claude_cli import ChatClaudeCli
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_SYSTEM_PROMPT = (
    "You are a training assistant with access to Strava tools. "
    "Use the tools to fetch real workout and activity data before answering."
)


class StravaAgent:
    """Conversational agent: Claude + Strava MCP tools in a LangGraph tool loop.

    The graph is built on the first ask() (building needs `await`, which
    __init__ can't do). Conversation memory is kept per thread_id.

    extra_tools are local LangChain tools offered alongside the Strava MCP
    tools, e.g. plot_tools.PLOT_TOOLS for charts.
    """

    def __init__(
        self,
        llm: BaseChatModel | None = None,
        *,
        mcp_url: str = DEFAULT_MCP_URL,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        extra_tools: Sequence[BaseTool] = (),
    ) -> None:
        self.llm = llm or ChatClaudeCli(model="claude-sonnet-4-5", max_tokens=1024)
        self.mcp_url = mcp_url
        self.system_prompt = system_prompt
        self.extra_tools = list(extra_tools)
        self.thread_id = str(uuid.uuid4())
        self._graph: CompiledStateGraph | None = None
        self._build_lock = asyncio.Lock()

    async def ask(self, question: str) -> str:
        """Ask a question in the current conversation; returns the answer text."""
        return self.answer(await self.ainvoke(question))

    async def ainvoke(self, question: str) -> dict:
        """Like ask(), but returns the full graph state (all messages, tool calls)."""
        graph = await self.graph()
        return await graph.ainvoke(
            {"messages": [{"role": "user", "content": question}]},
            {"configurable": {"thread_id": self.thread_id}},
        )

    def reset(self) -> None:
        """Start a new conversation (previous threads stay in memory, unused)."""
        self.thread_id = str(uuid.uuid4())

    async def graph(self) -> CompiledStateGraph:
        """The compiled LangGraph, built on first use."""
        async with self._build_lock:
            if self._graph is None:
                self._graph = await self._build_graph()
            return self._graph

    async def _build_graph(self) -> CompiledStateGraph:
        await self._check_server()
        mcp_client = MultiServerMCPClient(
            {"strava": {"transport": "streamable_http", "url": self.mcp_url}}
        )
        tools = [*await mcp_client.get_tools(), *self.extra_tools]
        llm_with_tools = self.llm.bind_tools(tools)

        async def assistant_node(state: MessagesState) -> dict:
            messages = [
                {"role": "system", "content": self.system_prompt},
                *state["messages"],
            ]
            return {"messages": [await llm_with_tools.ainvoke(messages)]}

        builder = StateGraph(MessagesState)
        builder.add_node("assistant", assistant_node)
        builder.add_node("tools", ToolNode(tools))

        builder.add_edge(START, "assistant")
        builder.add_conditional_edges("assistant", tools_condition)
        builder.add_edge("tools", "assistant")

        return builder.compile(checkpointer=MemorySaver())

    async def _check_server(self) -> None:
        """Fail with a clear message if the MCP server isn't running.

        Otherwise the MCP client surfaces a hard-to-read ExceptionGroup.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.get(self.mcp_url)  # any HTTP response means it's up
        except httpx.ConnectError:
            raise RuntimeError(
                f"Strava MCP server not reachable at {self.mcp_url}. "
                "Start it with `uv run python strava_mcp_server.py`."
            ) from None

    @classmethod
    def answer(cls, state: dict) -> str:
        """The final answer text from a graph state returned by ainvoke()."""
        return cls.text(cls._drop_continuation_reply(state["messages"][-1]))

    @staticmethod
    def _drop_continuation_reply(message: AIMessage) -> AIMessage:
        """Keep only the first reply in a message that contains two.

        Workaround for langchain-claude-cli 1.2.1: after a tool cycle it resumes
        the CLI session with a "Continue from where you left off." prompt, so
        the model answers, then answers again ("I've already answered...") and
        both land in one AIMessage as [thinking, text, thinking, text]. A
        thinking block that follows text marks the start of the second reply.
        Remove this once the library is fixed.

        Only apply this to output, never to messages stored in graph state:
        ChatClaudeCli matches the history against its session cache, and an
        edited message breaks follow-up turns (max-turns errors, skipped tools).
        """
        if message.tool_calls or not isinstance(message.content, list):
            return message

        def is_text(block: object) -> bool:
            return isinstance(block, str) or (
                isinstance(block, dict) and block.get("type") == "text"
            )

        first_reply: list = []
        for block in message.content:
            starts_new_reply = isinstance(block, dict) and block.get("type") in (
                "thinking",
                "redacted_thinking",
            )
            if starts_new_reply and any(is_text(b) for b in first_reply):
                return message.model_copy(update={"content": first_reply})
            first_reply.append(block)
        return message

    @staticmethod
    def text(message: BaseMessage) -> str:
        """Extract the text from a message, skipping thinking/tool-use blocks."""
        if isinstance(message.content, str):
            return message.content
        return "".join(
            block["text"] if isinstance(block, dict) else block
            for block in message.content
            if isinstance(block, str)
            or (isinstance(block, dict) and block.get("type") == "text")
        )
