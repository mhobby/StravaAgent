"""Streamlit chat frontend for StravaAgent, with line and bar chart tools.

Prerequisites:
  * Claude CLI logged in (`claude auth login`, or claude_login() in auth.py).
  * The Strava MCP server running: `uv run python strava_mcp_server.py`.

Run:
    uv run streamlit run app.py
"""

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

import streamlit as st
from langchain_claude_cli import ChatClaudeCli
from langchain_core.messages import AIMessage, HumanMessage

from plot_tools import (
    PLOT_PROMPT,
    PLOT_TOOLS,
    chart_table,
    charts_from_messages,
    render_chart,
)
from strava_agent import DEFAULT_SYSTEM_PROMPT, StravaAgent

T = TypeVar("T")

EXAMPLES = [
    "Bar chart of my weekly running distance for the last 8 weeks",
    "Line chart of average heart rate across my last 10 runs",
    "Compare my moving time per sport this year as a bar chart",
]


@st.cache_resource
def _event_loop() -> asyncio.AbstractEventLoop:
    """One long-lived event loop for the app, on a background thread.

    Streamlit reruns this script synchronously on every interaction. The agent
    (and the CLI sessions it caches) must stay on a single loop, so all agent
    coroutines are submitted here instead of using asyncio.run() per rerun.
    """
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run_coroutine_threadsafe(coro, _event_loop()).result()


def new_agent() -> StravaAgent:
    return StravaAgent(
        # Charts send their data as tool arguments, so allow longer outputs.
        ChatClaudeCli(model="claude-sonnet-4-5", max_tokens=4096),
        system_prompt=f"{DEFAULT_SYSTEM_PROMPT}\n\n{PLOT_PROMPT}",
        extra_tools=PLOT_TOOLS,
    )


def current_turn(state: dict) -> list:
    """Messages produced for the latest question (after the last user message)."""
    messages = state["messages"]
    start = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    return messages[start + 1 :]


def tool_steps(turn: list) -> list[str]:
    return [
        f"`{call['name']}` {call['args']}"
        for m in turn
        if isinstance(m, AIMessage)
        for call in m.tool_calls
    ]


def show_reply(reply: dict) -> None:
    """Render one assistant reply: text, charts (+ data tables), tool steps."""
    st.markdown(reply["content"])
    theme = st.context.theme.type or "light"
    for spec in reply.get("charts", []):
        st.altair_chart(render_chart(spec, theme), width="stretch", alt=spec["title"])
        with st.expander(f"Data: {spec['title']}"):
            st.dataframe(chart_table(spec), hide_index=True)
    if reply.get("steps"):
        with st.expander(f"Strava calls ({len(reply['steps'])})"):
            st.markdown("\n".join(f"- {s}" for s in reply["steps"]))


st.set_page_config(page_title="Strava Agent", page_icon="🏃")

if "agent" not in st.session_state:
    st.session_state.agent = new_agent()
    st.session_state.history = []

with st.sidebar:
    st.header("Strava Agent")
    st.caption("Ask about your training. Ask for a line or bar chart to plot it.")
    if st.button("New conversation", icon=":material/refresh:", width="stretch"):
        st.session_state.agent.reset()
        st.session_state.history = []
        st.rerun()
    st.subheader("Try")
    for example in EXAMPLES:
        if st.button(example, width="stretch"):
            st.session_state.pending = example

st.title("Strava Agent")

for entry in st.session_state.history:
    with st.chat_message(entry["role"]):
        if entry["role"] == "user":
            st.markdown(entry["content"])
        else:
            show_reply(entry)

prompt = st.chat_input("Ask about your training…") or st.session_state.pop(
    "pending", None
)
if prompt:
    st.session_state.history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Checking Strava…"):
                agent: StravaAgent = st.session_state.agent
                state = run(agent.ainvoke(prompt))
        except Exception as e:  # noqa: BLE001 - UI boundary: show it, keep app alive
            st.error(f"{type(e).__name__}: {e}")
        else:
            turn = current_turn(state)
            reply = {
                "role": "assistant",
                "content": agent.answer(state),
                "charts": charts_from_messages(turn),
                "steps": tool_steps(turn),
            }
            st.session_state.history.append(reply)
            show_reply(reply)
