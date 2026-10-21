"""Daily top-matches bot (Sportmonks): football + cricket -> ONE Telegram message.

No odds. Lists up to 8 matches per sport so you can make your own picks.
Football: ranked by the standings gap between the two teams.
Cricket: listed in order of start time.

Needs env vars: TELEGRAM_TOKEN, CHAT_ID, and either SPORTMONKS_TOKEN or API_FOOTBALL_KEY
(your Sportmonks API token). Usage: python daily_slip.py [both|football|cricket]
"""
import os
import sys
import html
import datetime as dt
from zoneinfo import ZoneInfo

import requests

# ================= SETTINGS (edit these) =================
TZ = "Africa/Kampala"
TOP_N = 8                          # matches per sport
FOOTBALL_DAYS_AHEAD = 1            # 1 = tomorrow's matches (Kampala date), 0 = today's
CRICKET_WINDOW_HOURS = 36          # cricket matches starting within this many hours
# =========================================================

FB_BASE = "https://api.sportmonks.com/v3/football"
CR_BASE = "https://cricket.sportmonks.com/api/v2.0"
TOKEN = os.environ.get("SPORTMONKS_TOKEN") or os.environ.get("API_FOOTBALL_KEY") or ""
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

ERRORS = []


def api(url, **params):
    """GET a Sportmonks endpoint; returns the parsed JSON (or {} on failure)."""
    params["api_token"] = TOKEN
    try:
        r = requests.get(url, params=params, timeout=30)
    except requests.RequestException as e:
        ERRORS.append(f"network: {e}"[:150])
        return {}
    try:
        data = r.json()
    except ValueError:
        ERRORS.append(f"HTTP {r.status_code}: not JSON")
        return {}
    if r.status_code != 200 or (data.get("message") and not data.get("data")):
        msg = str(data.get("message") or f"HTTP {r.status_code}")[:200]
        print("API problem:", url, msg)
        if msg not in ERRORS:
            ERRORS.append(msg)
    return data


def get_all(url, **params):
    """Follow pagination (up to 5 pages) and return the combined 'data' list."""
    out = []
    for page in range(1, 6):
        d = api(url, page=page, **params)
        rows = d.get("data") or []
        out += rows if isinstance(rows, list) else []
        pg = d.get("pagination") or d.get("meta", {}).get("pagination") or {}
        if not pg.get("has_more") and not (pg.get("current_page", 1) < pg.get("total_pages", 1)):
            break
    return out


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=30,
    )
    print("Telegram:", r.status_code, r.text[:200])


def to_local(utc_str):
    """'2026-10-22 16:00:00' or ISO-with-Z (UTC) -> aware datetime in Kampala time."""
    s = utc_str.replace("Z", "+00:00").replace(" ", "T")
    t = dt.datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(ZoneInfo(TZ))


# ---------------- football ----------------
def football_section(now):
    target = (now + dt.timedelta(days=FOOTBALL_DAYS_AHEAD)).date()
    title = f"<b>FOOTBALL - {target.isoformat()}</b>"

    # UTC dates can differ from Kampala dates, so fetch two days and filter locally
    start = (target - dt.timedelta(days=1)).isoformat()
    rows = get_all(f"{FB_BASE}/fixtures/between/{start}/{target.isoformat()}",
                   include="participants;league")
    fixtures = []
    for f in rows:
        if not f.get("starting_at"):
            continue
        when = to_local(f["starting_at"])
        if when.date() == target and f.get("state_id", 1) == 1:
            fixtures.append((when, f))
    fixtures.sort(key=lambda x: x[0])
    print(f"[football] {len(fixtures)} fixtures")
    if not fixtures:
        return title + "\nNo fixtures found in the leagues your plan covers."

    # Standings per season -> {team_id: (position, points)}
    table = {}
    for sid in {f.get("season_id") for _, f in fixtures if f.get("season_id")}:
        for row in get_all(f"{FB_BASE}/standings/seasons/{sid}"):
            try:
                table.setdefault(row["participant_id"], (int(row["position"]), int(row["points"])))
            except (KeyError, TypeError, ValueError):
                continue

    scored = []
    for when, f in fixtures:
        teams = {p.get("meta", {}).get("location"): p for p in f.get("participants", [])}
        home, away = teams.get("home"), teams.get("away")
        if not home or not away:
            continue
        hs, as_ = table.get(home["id"]), table.get(away["id"])
        gap = abs(hs[0] - as_[0]) if hs and as_ else -1
        scored.append((gap, when, f, home, away, hs, as_))
    scored.sort(key=lambda x: (-x[0], x[1]))

    lines = [title]
    for i, (gap, when, f, home, away, hs, as_) in enumerate(scored[:TOP_N], 1):
        league = (f.get("league") or {}).get("name", "")
        line = (f"{i}. <b>{html.escape(home['name'])} vs {html.escape(away['name'])}</b>\n"
                f"   {html.escape(league)}, {when.strftime('%a %H:%M')} EAT")
        if hs and as_:
            line += (f"\n   Table: {html.escape(home['name'])} #{hs[0]} ({hs[1]} pts)"
                     f" | {html.escape(away['name'])} #{as_[0]} ({as_[1]} pts)")
        lines.append(line)
    return "\n".join(lines)


# ---------------- cricket ----------------
def cricket_section(now):
    end = now + dt.timedelta(hours=CRICKET_WINDOW_HOURS)
    title = f"<b>CRICKET - {now.strftime('%Y-%m-%d')}</b>"

    rows = get_all(f"{CR_BASE}/fixtures", include="localteam,visitorteam,league",
                   **{"filter[starts_between]": f"{now.date().isoformat()},{end.date().isoformat()}"})
    games = []
    for f in rows:
        if not f.get("starting_at") or f.get("status") not in ("NS", None):
            continue
        when = to_local(f["starting_at"])
        if now < when <= end:
            games.append((when, f))
    games.sort(key=lambda x: x[0])
    print(f"[cricket] {len(games)} matches in window")
    if not games:
        return title + "\nNo matches found in the next 36 hours (in the leagues your plan covers)."

    lines = [title]
    for i, (when, f) in enumerate(games[:TOP_N], 1):
        home = (f.get("localteam") or {}).get("name", "Team A")
        away = (f.get("visitorteam") or {}).get("name", "Team B")
        league = (f.get("league") or {}).get("name", "")
        kind = f.get("type") or ""
        extra = ", ".join(x for x in (league, kind) if x)
        lines.append(f"{i}. <b>{html.escape(home)} vs {html.escape(away)}</b>\n"
                     f"   {html.escape(extra)}, {when.strftime('%a %H:%M')} EAT")
    return "\n".join(lines)


# ---------------- main ----------------
def main():
    arg = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SPORT", "both")).strip().lower()
    if arg not in ("both", "football", "cricket", ""):
        raise SystemExit("Usage: python daily_slip.py [both|football|cricket]")
    sports = ["football", "cricket"] if arg in ("both", "") else [arg]
    if not TOKEN:
        raise SystemExit("Missing SPORTMONKS_TOKEN (or API_FOOTBALL_KEY) secret")

    now = dt.datetime.now(ZoneInfo(TZ))
    sections = []
    for sport in sports:
        try:
            sections.append(football_section(now) if sport == "football" else cricket_section(now))
        except Exception as e:
            print(sport, "failed:", repr(e))
            sections.append(f"<b>{sport.upper()}</b>\nCould not load data (see GitHub Actions log).")

    parts = [f"<b>Top matches - {now.strftime('%a %d %b')}</b>"] + sections
    if ERRORS:
        parts.append("API problems: " + html.escape(" | ".join(ERRORS)))
    parts.append("A quick list to start from, not a prediction. Do your own checks.")

    message = "\n\n-----\n\n".join(parts)
    if len(message) <= 4000:
        send(message)
    else:  # Telegram's limit is 4096 characters per message
        for part in parts:
            send(part)


if __name__ == "__main__":
    main()