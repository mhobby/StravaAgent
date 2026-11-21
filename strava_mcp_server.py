"""A small local MCP server wrapping the Strava REST API (v3).

Start it in its own terminal before running the agent:

    uv run python strava_mcp_server.py

It serves streamable HTTP at http://127.0.0.1:8000/mcp (localhost only; there
is no auth on this endpoint, so don't bind it to other interfaces). On startup
it logs in to Strava via the browser if no tokens are saved yet.

Inspect interactively:    uv run mcp dev strava_mcp_server.py

Responses are trimmed and converted to friendly units (km, minutes, km/h) to
keep the LLM's context small.
"""

import asyncio
from datetime import datetime
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from auth import RefreshingOAuthAuth, ensure_tokens

STRAVA_API = "https://www.strava.com/api/v3"


class StravaAPI:
    """Strava REST client that logs in (via the browser if needed) on first use.

    Lazy rather than at startup so the server always answers the MCP
    handshake / list_tools promptly; only a real tool call waits on login.
    """

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def _client(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._http is None:
                # The login flow blocks on a local HTTP callback, so run it off-loop.
                tokens = await asyncio.to_thread(ensure_tokens)
                self._http = httpx.AsyncClient(
                    base_url=STRAVA_API,
                    auth=RefreshingOAuthAuth(tokens),
                    timeout=30.0,
                )
            return self._http

    async def get(self, path: str, **params: Any) -> Any:
        client = await self._client()
        resp = await client.get(
            path, params={k: v for k, v in params.items() if v is not None}
        )
        if resp.status_code == 429:
            raise RuntimeError(
                "Strava rate limit hit (100 req/15 min). Try again later."
            )
        resp.raise_for_status()
        return resp.json()


# Stateless: the LangChain MCP adapter opens a new session per tool call anyway,
# and stateful mode logs a ClosedResourceError traceback each time one closes.
mcp = FastMCP("strava", host="127.0.0.1", port=8000, stateless_http=True)
strava = StravaAPI()


def _epoch(date: str | None) -> int | None:
    """'YYYY-MM-DD' (or full ISO datetime, local time) -> Unix timestamp."""
    return int(datetime.fromisoformat(date).timestamp()) if date else None


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None else None


def _summarize_activity(a: dict) -> dict:
    """Keep the fields a training assistant cares about, in friendly units."""
    return {
        "id": a["id"],
        "name": a.get("name"),
        "sport_type": a.get("sport_type") or a.get("type"),
        "start_date_local": a.get("start_date_local"),
        "distance_km": _round(a.get("distance", 0) / 1000, 2),
        "moving_time_min": _round(a.get("moving_time", 0) / 60),
        "elapsed_time_min": _round(a.get("elapsed_time", 0) / 60),
        "elevation_gain_m": _round(a.get("total_elevation_gain")),
        "average_speed_kmh": _round((a.get("average_speed") or 0) * 3.6),
        "max_speed_kmh": _round((a.get("max_speed") or 0) * 3.6),
        "average_heartrate": a.get("average_heartrate"),
        "max_heartrate": a.get("max_heartrate"),
        "average_watts": a.get("average_watts"),
        "average_cadence": a.get("average_cadence"),
        "suffer_score": a.get("suffer_score"),
        "kudos_count": a.get("kudos_count"),
        "trainer": a.get("trainer"),
    }


def _summarize_totals(t: dict) -> dict:
    return {
        "count": t.get("count"),
        "distance_km": _round(t.get("distance", 0) / 1000),
        "moving_time_hours": _round(t.get("moving_time", 0) / 3600),
        "elevation_gain_m": _round(t.get("elevation_gain")),
    }


@mcp.tool()
async def get_athlete() -> dict:
    """Get the authenticated athlete's profile.

    Use this for personal context (weight, FTP, equipment) when interpreting
    training data, e.g. power-to-weight or how worn a pair of shoes is.

    Returns:
        id: Strava athlete id.
        name: First and last name.
        city, country: Location from the profile (may be null).
        sex: "M", "F" or null.
        weight_kg: Body weight in kg (null if not set).
        ftp: Functional threshold power in watts (null if not set).
        measurement_preference: "meters" or "feet", the athlete's display
            preference. Values from these tools are always metric regardless.
        bikes, shoes: Gear with total distance_km logged on each.
    """
    a = await strava.get("/athlete")
    return {
        "id": a["id"],
        "name": f"{a.get('firstname', '')} {a.get('lastname', '')}".strip(),
        "city": a.get("city"),
        "country": a.get("country"),
        "sex": a.get("sex"),
        "weight_kg": a.get("weight"),
        "ftp": a.get("ftp"),
        "measurement_preference": a.get("measurement_preference"),
        "bikes": [
            {"name": b.get("name"), "distance_km": _round(b.get("distance", 0) / 1000)}
            for b in a.get("bikes") or []
        ],
        "shoes": [
            {"name": s.get("name"), "distance_km": _round(s.get("distance", 0) / 1000)}
            for s in a.get("shoes") or []
        ],
    }


@mcp.tool()
async def get_athlete_stats() -> dict:
    """Get aggregate ride, run and swim totals over three time windows.

    Use this for volume questions ("how much have I run this year?") instead
    of paging through list_activities. Strava only counts activities whose
    visibility is "Everyone", so private activities are missing from these
    totals; use list_activities when exact numbers matter.

    Returns:
        biggest_ride_distance_km: Longest single ride ever.
        biggest_climb_elevation_gain_m: Biggest single-climb elevation gain.
        {period}_{sport}_totals: For period in recent (last 4 weeks), ytd
            (calendar year to date), all (all time) and sport in ride, run,
            swim. Each has count, distance_km, moving_time_hours and
            elevation_gain_m.
    """
    athlete = await strava.get("/athlete")
    s = await strava.get(f"/athletes/{athlete['id']}/stats")
    out: dict[str, Any] = {
        "biggest_ride_distance_km": _round(
            (s.get("biggest_ride_distance") or 0) / 1000
        ),
        "biggest_climb_elevation_gain_m": _round(s.get("biggest_climb_elevation_gain")),
    }
    for sport in ("ride", "run", "swim"):
        for period in ("recent", "ytd", "all"):
            key = f"{period}_{sport}_totals"
            if key in s:
                out[key] = _summarize_totals(s[key])
    return out


@mcp.tool()
async def list_activities(
    after: str | None = None,
    before: str | None = None,
    page: int = 1,
    per_page: int = 30,
) -> list[dict]:
    """List the athlete's activities as summaries, newest first.

    Use this to find activities in a date range or the most recent ones, then
    pass an activity's `id` to get_activity, get_activity_laps or
    get_activity_zones for detail. Results are paginated: a page with fewer
    than per_page items is the last one. Never assume a short page (e.g.
    per_page=1) is the athlete's whole history; fetch more if needed.

    Args:
        after: Only activities that started after this point. 'YYYY-MM-DD'
            (midnight, server local time) or a full ISO datetime such as
            '2026-11-01T06:00'. Omit for no lower bound.
        before: Only activities that started before this point, same format
            as `after`. Omit for no upper bound. Combine both for a range.
        page: 1-based page number for paging through older results.
        per_page: Activities per page, 1-200 (values above 200 are capped).
            Use a large value when summarising a date range.

    Returns:
        One summary per activity with: id, name, sport_type (e.g. "Run",
        "Ride", "VirtualRide", "Swim", "WeightTraining"), start_date_local
        (athlete's local wall-clock time; ignore the trailing "Z"),
        distance_km, moving_time_min, elapsed_time_min (includes stops),
        elevation_gain_m, average_speed_kmh, max_speed_kmh (for running pace
        in min/km use 60 / speed), average_heartrate and max_heartrate (bpm),
        average_watts, average_cadence (rpm for rides, steps per minute per
        leg for runs), suffer_score (Strava "Relative Effort"), kudos_count,
        trainer (true for indoor/trainer sessions). Sensor fields are null
        when the activity has no such data.
    """
    activities = await strava.get(
        "/athlete/activities",
        after=_epoch(after),
        before=_epoch(before),
        page=page,
        per_page=min(per_page, 200),
    )
    return [_summarize_activity(a) for a in activities]


@mcp.tool()
async def get_activity(activity_id: int) -> dict:
    """Get full details for one activity.

    Use this when the summary from list_activities isn't enough, e.g. to read
    the athlete's notes, check how effort varied per kilometre, or see which
    gear was used.

    Args:
        activity_id: The activity's `id` from list_activities. Must be one of
            the athlete's own activities.

    Returns:
        Every field from the list_activities summary, plus:
        description: The athlete's own notes on the activity (may be null).
        calories: Energy estimate in kcal.
        gear: Name of the bike or shoes used (null if none recorded).
        device_name: Recording device, e.g. "Garmin Forerunner 265".
        perceived_exertion: Athlete-rated effort 1-10 (null if not rated).
        splits_metric: Per-kilometre splits (mainly runs; often empty for rides),
            each with split number, distance_km, moving_time_min (pace in
            min/km for a full 1 km split), elevation_difference_m,
            average_heartrate and pace_zone (Strava pace zone number).
    """
    a = await strava.get(f"/activities/{activity_id}")
    out = _summarize_activity(a)
    out.update(
        {
            "description": a.get("description"),
            "calories": a.get("calories"),
            "gear": (a.get("gear") or {}).get("name"),
            "device_name": a.get("device_name"),
            "perceived_exertion": a.get("perceived_exertion"),
            "splits_metric": [
                {
                    "split": sp.get("split"),
                    "distance_km": _round(sp.get("distance", 0) / 1000, 2),
                    "moving_time_min": _round(sp.get("moving_time", 0) / 60, 2),
                    "elevation_difference_m": sp.get("elevation_difference"),
                    "average_heartrate": sp.get("average_heartrate"),
                    "pace_zone": sp.get("pace_zone"),
                }
                for sp in a.get("splits_metric") or []
            ],
        }
    )
    return out


@mcp.tool()
async def get_activity_laps(activity_id: int) -> list[dict]:
    """Get the laps recorded for one activity.

    Use this to analyse structured workouts such as intervals or repeats,
    where each lap is a work or recovery segment. Activities without manual
    laps usually have auto-laps (e.g. every 1 km or 1 mile) or one lap.

    Args:
        activity_id: The activity's `id` from list_activities.

    Returns:
        One entry per lap, in order: lap_index (1-based), name, distance_km,
        moving_time_min, average_speed_kmh (running pace in min/km is
        60 / speed), average_heartrate and max_heartrate (bpm),
        average_watts, average_cadence, elevation_gain_m. Sensor fields are
        null when not recorded.
    """
    laps = await strava.get(f"/activities/{activity_id}/laps")
    return [
        {
            "lap_index": lap.get("lap_index"),
            "name": lap.get("name"),
            "distance_km": _round(lap.get("distance", 0) / 1000, 2),
            "moving_time_min": _round(lap.get("moving_time", 0) / 60, 2),
            "average_speed_kmh": _round((lap.get("average_speed") or 0) * 3.6),
            "average_heartrate": lap.get("average_heartrate"),
            "max_heartrate": lap.get("max_heartrate"),
            "average_watts": lap.get("average_watts"),
            "average_cadence": lap.get("average_cadence"),
            "elevation_gain_m": lap.get("total_elevation_gain"),
        }
        for lap in laps
    ]


@mcp.tool()
async def get_activity_zones(activity_id: int) -> list[dict]:
    """Get the time spent in each heart-rate and power zone for one activity.

    Use this to judge training intensity, e.g. whether an easy run stayed in
    zone 2. Strava only provides this for athletes with a subscription, and
    only for activities with heart-rate or power data; otherwise it returns
    an empty list or an error.

    Args:
        activity_id: The activity's `id` from list_activities.

    Returns:
        One entry per zone type, with type ("heartrate", in bpm, or "power",
        in watts) and buckets listed from lowest to highest zone. Each bucket
        has min, max (-1 means no upper limit) and time_min spent in it.
    """
    zones = await strava.get(f"/activities/{activity_id}/zones")
    return [
        {
            "type": z.get("type"),
            "buckets": [
                {
                    "min": b.get("min"),
                    "max": b.get("max"),
                    "time_min": _round(b.get("time", 0) / 60),
                }
                for b in z.get("distribution_buckets") or []
            ],
        }
        for z in zones
    ]


if __name__ == "__main__":
    ensure_tokens()  # log in now (if needed) rather than mid-conversation
    mcp.run(transport="streamable-http")
