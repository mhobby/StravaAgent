"""Chart tools the agent can call to show line and bar charts in the UI.

The tools don't draw anything themselves: each returns a short confirmation
for the model plus a chart spec as the ToolMessage *artifact* (LangChain's
"content_and_artifact" format). The frontend collects those artifacts from
the turn's messages and renders them with render_chart().
"""

from typing import Literal

import altair as alt
import pandas as pd
from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

ChartKind = Literal["line", "bar"]
Theme = Literal["light", "dark"]

# Categorical palette (fixed order; validated for colour-vision deficiency in
# both modes). Slot order is the safety mechanism, so never shuffle or cycle.
PALETTE: dict[Theme, list[str]] = {
    "light": [
        "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
        "#e87ba4", "#008300", "#6250d6", "#e34948",
    ],
    "dark": [
        "#3987e5", "#d95926", "#199e70", "#c98500",
        "#d55181", "#0ca30c", "#9085e9", "#e66767",
    ],
}  # fmt: skip
# Streamlit's default page backgrounds, used for the 2px gap between bars.
SURFACE: dict[Theme, str] = {"light": "#ffffff", "dark": "#0e1117"}
MAX_SERIES = len(PALETTE["light"])

PLOT_PROMPT = (
    "You can show charts to the user with plot_line_chart (trends over time or "
    "any ordered x axis) and plot_bar_chart (comparing categories or per-period "
    "totals). Plot when the user asks for a chart, plot or graph, or when a "
    "chart clearly helps. Always fetch real data with the Strava tools first "
    "and never invent values; aggregate it yourself (e.g. weekly totals) before "
    "plotting. The chart appears directly below your reply, so keep the text "
    "short and don't repeat every plotted number."
)


class Series(BaseModel):
    """One named series of y values, aligned with the chart's x values."""

    name: str = Field(description="Legend label, e.g. 'Run' or 'Distance (km)'.")
    values: list[float | None] = Field(
        description="One y value per x entry, in the same order; null for a gap."
    )


def _make_spec(
    kind: ChartKind,
    title: str,
    x: list[str],
    series: list[Series] | list[dict],
    x_label: str,
    y_label: str,
) -> tuple[str, dict | None]:
    """Validate the inputs; return (message for the model, chart spec or None)."""
    parsed = [s if isinstance(s, Series) else Series(**s) for s in series]
    if not x or not parsed:
        return "Error: provide at least one x value and one series.", None
    if len(parsed) > MAX_SERIES:
        error = (
            f"Error: at most {MAX_SERIES} series per chart. Combine the smallest "
            "into an 'Other' series or split into several charts."
        )
        return error, None
    for s in parsed:
        if len(s.values) != len(x):
            error = (
                f"Error: series '{s.name}' has {len(s.values)} values but x has "
                f"{len(x)} entries; they must match one-to-one."
            )
            return error, None
    spec = {
        "kind": kind,
        "title": title,
        "x": x,
        "series": [s.model_dump() for s in parsed],
        "x_label": x_label,
        "y_label": y_label,
    }
    message = (
        f"Displayed {kind} chart '{title}' to the user "
        f"({len(parsed)} series, {len(x)} points)."
    )
    return message, spec


@tool(response_format="content_and_artifact", parse_docstring=True)
def plot_line_chart(
    title: str,
    x: list[str],
    series: list[Series],
    x_label: str = "",
    y_label: str = "",
) -> tuple[str, dict | None]:
    """Show a line chart to the user, for trends over time or any ordered x axis.

    Use for things like weekly distance over a training block, heart rate
    across recent runs, or pace progression. Put several related measures with
    the same unit on one chart as separate series; never mix units (e.g. km
    and bpm) on one chart, make two charts instead.

    Args:
        title: Short chart title saying what is plotted, e.g. "Weekly running
            distance, last 8 weeks".
        x: X-axis values in display order. Use 'YYYY-MM-DD' dates for time
            series (plotted on a real time axis), otherwise short labels such
            as "Wk 1" or activity names.
        series: Up to 8 series, each a name and one value per x entry.
        x_label: X-axis title, e.g. "Week starting". Optional.
        y_label: Y-axis title including the unit, e.g. "Distance (km)".
    """
    return _make_spec("line", title, x, series, x_label, y_label)


@tool(response_format="content_and_artifact", parse_docstring=True)
def plot_bar_chart(
    title: str,
    x: list[str],
    series: list[Series],
    x_label: str = "",
    y_label: str = "",
) -> tuple[str, dict | None]:
    """Show a bar chart to the user, for comparing amounts across categories.

    Use for things like distance per sport, time per week or month, or
    activity counts by day of the week. Several series are drawn as grouped
    bars side by side (e.g. Run vs Ride per month); never mix units on one
    chart, make two charts instead.

    Args:
        title: Short chart title saying what is plotted, e.g. "Distance by
            sport, 2026".
        x: Category labels in display order, e.g. ["Jan", "Feb", "Mar"] or
            ["Run", "Ride", "Swim"]. Bars keep this order.
        series: Up to 8 series, each a name and one value per category.
        x_label: X-axis title, e.g. "Month". Optional.
        y_label: Y-axis title including the unit, e.g. "Moving time (h)".
    """
    return _make_spec("bar", title, x, series, x_label, y_label)


PLOT_TOOLS = [plot_line_chart, plot_bar_chart]
PLOT_TOOL_NAMES = {t.name for t in PLOT_TOOLS}


def charts_from_messages(messages: list[BaseMessage]) -> list[dict]:
    """Chart specs from the plot tools' ToolMessages, in order.

    Only the plot tools' artifacts are charts: MCP tool messages carry
    artifacts too (structured Strava data). Charts with the same kind and
    title are de-duplicated, keeping the last, because langchain-claude-cli
    1.2.1's duplicate-reply quirk (see StravaAgent._drop_continuation_reply)
    can make the model issue the same plot call twice in one turn.
    """
    charts: dict[tuple[str, str], dict] = {}
    for m in messages:
        if isinstance(m, ToolMessage) and m.name in PLOT_TOOL_NAMES and m.artifact:
            charts[(m.artifact["kind"], m.artifact["title"])] = m.artifact
    return list(charts.values())


def chart_table(spec: dict) -> pd.DataFrame:
    """The chart's data as a wide table (one column per series), for display."""
    table = pd.DataFrame({s["name"]: s["values"] for s in spec["series"]})
    table.insert(0, spec["x_label"] or "x", spec["x"])
    return table


def _long_frame(spec: dict) -> tuple[pd.DataFrame, bool]:
    """Long-form data for Altair, and whether x parsed as dates."""
    rows = [
        {"x": x, "series": s["name"], "value": v}
        for s in spec["series"]
        for x, v in zip(spec["x"], s["values"])
    ]
    df = pd.DataFrame(rows)
    try:
        df["x"] = pd.to_datetime(df["x"], format="ISO8601")
        return df, True
    except (ValueError, TypeError):
        return df, False


def render_chart(spec: dict, theme: Theme = "light") -> alt.LayerChart | alt.Chart:
    """Build the Altair chart for a spec produced by one of the plot tools."""
    df, is_time = _long_frame(spec)
    names = [s["name"] for s in spec["series"]]
    multi = len(names) > 1
    n_points = len(spec["x"])

    if is_time:
        # Tick exactly on the data's dates when few enough (e.g. one per week),
        # instead of Vega's default every-few-days ticks.
        dates = sorted(df["x"].dropna().unique())
        axis = alt.Axis(
            format="%-d %b",
            values=[d.isoformat() for d in pd.to_datetime(dates)]
            if n_points <= 12
            else alt.Undefined,
            labelAngle=0 if n_points <= 8 else -45,
            grid=False,
        )
        x_enc = alt.X("x:T", title=spec["x_label"] or None, axis=axis)
    else:
        x_enc = alt.X(
            "x:N",
            title=spec["x_label"] or None,
            sort=spec["x"],  # keep the model's category order
            axis=alt.Axis(labelAngle=0 if n_points <= 8 else -45, grid=False),
        )
    y_enc = alt.Y("value:Q", title=spec["y_label"] or None)
    color = alt.Color(
        "series:N",
        scale=alt.Scale(domain=names, range=PALETTE[theme][: len(names)]),
        # A legend whenever there are 2+ series; a lone series is named by the title.
        legend=alt.Legend(title=None, orient="top") if multi else None,
    )
    tooltip = [
        alt.Tooltip("x:T" if is_time else "x:N", title=spec["x_label"] or "x"),
        alt.Tooltip("series:N", title="Series"),
        alt.Tooltip("value:Q", title=spec["y_label"] or "Value", format=",.2f"),
    ]
    base = alt.Chart(df).encode(x=x_enc, y=y_enc, color=color)

    if spec["kind"] == "bar":
        chart = base.mark_bar(
            cornerRadiusEnd=4, stroke=SURFACE[theme], strokeWidth=2
        ).encode(tooltip=tooltip)
        if multi:
            chart = chart.encode(xOffset=alt.XOffset("series:N", sort=names))
    else:
        # Crosshair: the nearest x under the pointer lights up a rule and the
        # points of every series at that x; hovering a point shows its tooltip.
        hover = alt.selection_point(
            fields=["x"], nearest=True, on="pointerover", empty=False
        )
        line = base.mark_line(strokeWidth=2)
        # With few points, always show markers: a value next to a gap (null)
        # has no line segment and would otherwise be invisible.
        resting_opacity = 1 if n_points <= 24 else 0
        points = base.mark_circle(size=64).encode(
            opacity=alt.condition(hover, alt.value(1), alt.value(resting_opacity))
        )
        rule = (
            alt.Chart(df)
            .mark_rule(strokeWidth=1, color="#8a8984")
            .encode(x=x_enc)
            .transform_filter(hover)
        )
        # Invisible, larger-than-the-mark hit targets on top: they drive the
        # hover selection and carry the tooltip.
        catcher = (
            base.mark_point(size=400, opacity=0)
            .encode(tooltip=tooltip)
            .add_params(hover)
        )
        chart = alt.layer(line, rule, points, catcher)

    return chart.properties(title=spec["title"], height=320)
