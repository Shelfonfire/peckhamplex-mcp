import json
import re
from datetime import date, datetime, timezone, timedelta

import httpx
from mcp.server.fastmcp import FastMCP

import os

port = int(os.environ.get("PORT", "8080"))
mcp = FastMCP("peckhamplex", port=port, host="0.0.0.0", stateless_http=True, json_response=True)

BASE = "https://www.peckhamplex.london/api/v1/film"

UK_TZ = timezone(timedelta(hours=0))  # GMT baseline; BST handled below


def _uk_today() -> date:
    """Return today's date in UK time (handles GMT/BST)."""
    now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    # BST: last Sunday of March 01:00 UTC to last Sunday of October 01:00 UTC
    mar_last_sun = 31 - (datetime(year, 3, 31).weekday() + 1) % 7
    oct_last_sun = 31 - (datetime(year, 10, 31).weekday() + 1) % 7
    bst_start = datetime(year, 3, mar_last_sun, 1, tzinfo=timezone.utc)
    bst_end = datetime(year, 10, oct_last_sun, 1, tzinfo=timezone.utc)
    offset = timedelta(hours=1) if bst_start <= now_utc < bst_end else timedelta(hours=0)
    return (now_utc + offset).date()


_cache: dict[str, tuple[date, dict]] = {}


async def _fetch_by_title() -> dict:
    today = _uk_today()
    cached = _cache.get("by_title")
    if cached and cached[0] == today:
        return cached[1]
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{BASE}/by/title")
        r.raise_for_status()
        data = r.json()
    _cache["by_title"] = (today, data)
    return data


async def _fetch_by_dates() -> dict:
    today = _uk_today()
    cached = _cache.get("by_dates")
    if cached and cached[0] == today:
        return cached[1]
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{BASE}/by/dates")
        r.raise_for_status()
        data = r.json()
    _cache["by_dates"] = (today, data)
    return data


@mcp.tool()
async def get_all_films() -> str:
    """List all films currently showing or coming soon at Peckhamplex cinema."""
    data = await _fetch_by_title()
    return json.dumps(list(data.keys()), indent=2)


@mcp.tool()
async def get_screenings_by_film(title: str) -> str:
    """Get all upcoming screenings for a film. Case-insensitive substring match.

    Args:
        title: Film name or partial name to search for.
    """
    data = await _fetch_by_title()
    query = title.lower()
    matches = {}
    for film, dates in data.items():
        if query in film.lower():
            flat = []
            for day_label, showings in dates.items():
                for s in showings:
                    flat.append({
                        "date": day_label,
                        "time": s["time"],
                        "booking_url": s["url"],
                        "autism_friendly": s["autism"],
                        "hard_of_hearing": s["hoh"],
                        "watch_with_baby": s["wwb"],
                    })
            matches[film] = flat
    if not matches:
        return f"No films found matching '{title}'"
    return json.dumps(matches, indent=2)


@mcp.tool()
async def get_screenings_by_date(screening_date: str = "") -> str:
    """Get all screenings on a given date.

    Args:
        screening_date: Date in YYYY-MM-DD format. Defaults to today.
    """
    if screening_date:
        target = datetime.strptime(screening_date, "%Y-%m-%d").date()
    else:
        target = date.today()

    data = await _fetch_by_dates()

    # Match target date against the date keys (e.g. "Friday 20th March 2026")
    # Remove ordinal suffixes (1st, 2nd, 3rd, 4th etc) for parsing
    results = []
    for day_label, films in data.items():
        clean_label = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", day_label)
        try:
            label_date = datetime.strptime(clean_label, "%A %d %B %Y").date()
        except ValueError:
            continue
        if label_date != target:
            continue
        for film_id, showings in films.items():
            for s in showings:
                results.append({
                    "title": s["title"],
                    "time": s["time"],
                    "booking_url": s["url"],
                    "autism_friendly": s["autism"],
                    "hard_of_hearing": s["hoh"],
                    "watch_with_baby": s["wwb"],
                })

    if not results:
        return f"No screenings found for {target.isoformat()}"

    results.sort(key=lambda x: x["time"])
    return json.dumps({"date": target.isoformat(), "screenings": results}, indent=2)


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
