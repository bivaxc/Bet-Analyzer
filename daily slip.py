"""Daily betslip bot (football slip and basketball slip, sent separately).

Usage: python daily_slip.py football   |   python daily_slip.py basketball

API-Sports data -> best 2-3 legs with total odds 2.00-2.50 -> Telegram.
Needs env vars: API_FOOTBALL_KEY, TELEGRAM_TOKEN, CHAT_ID
(the same API-Sports key works for football and basketball).
"""
import os
import sys
import json
import html
import itertools
import datetime as dt
from zoneinfo import ZoneInfo

import requests

# ================= SETTINGS (edit these) =================
TZ = "Africa/Kampala"

FOOTBALL_LEAGUES = {                        # API-Football league ids
    39: "Premier League",
    140: "La Liga",
    135: "Serie A",
    78: "Bundesliga",
    61: "Ligue 1",
    2: "Champions League",
    3: "Europa League",
}
BASKETBALL_LEAGUES = {                      # API-Basketball league ids
    12: "NBA",
    120: "EuroLeague",
}

PREFERRED_BOOKMAKERS = ["Bet365", "1xBet", "Pinnacle", "Betway"]
TARGET_MIN, TARGET_MAX = 2.00, 2.50         # total slip odds
LEG_MIN, LEG_MAX = 1.20, 1.60               # odds allowed per leg
MIN_PROB = 0.58                             # min estimated win chance per leg
FOOTBALL_MIN_EV = -0.02                     # min value per football leg
BASKETBALL_MIN_EV = -0.04                   # basketball uses market odds, so slightly looser
MAX_PREDICTION_CALLS = 25                   # protects the 100/day free football quota
BASKETBALL_WINDOW_HOURS = 20                # games starting within this many hours
# =========================================================

FB_BASE = "https://v3.football.api-sports.io"
BB_BASE = "https://v1.basketball.api-sports.io"
API_KEY = os.environ["API_FOOTBALL_KEY"]
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]


# ---------------- helpers ----------------
def api(base, endpoint, **params):
    r = requests.get(base + endpoint, headers={"x-apisports-key": API_KEY},
                     params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        print("API error on", base, endpoint, data["errors"])
    return data


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=30,
    )
    print("Telegram:", r.status_code, r.text[:200])


def pick_bookmaker_market(bookmakers, match_bet):
    """Return (bookmaker name, {value: odd}) using preferred bookmaker order.
    match_bet(bet) -> True for the market we want."""
    for name in PREFERRED_BOOKMAKERS + [None]:
        for b in bookmakers:
            if name is None or b["name"].lower() == name.lower():
                for bet in b.get("bets", []):
                    if match_bet(bet):
                        try:
                            return b["name"], {v["value"]: float(v["odd"]) for v in bet["values"]}
                        except (ValueError, KeyError):
                            continue
    return None, {}


def local_time(iso):
    return dt.datetime.fromisoformat(iso).astimezone(ZoneInfo(TZ)).strftime("%a %H:%M")


# ---------------- football ----------------
def football_legs(now):
    date_str = now.strftime("%Y-%m-%d")
    season = now.year if now.month >= 7 else now.year - 1

    data = api(FB_BASE, "/fixtures", date=date_str, timezone=TZ)
    fixtures = [f for f in data.get("response", [])
                if f["league"]["id"] in FOOTBALL_LEAGUES
                and f["fixture"]["status"]["short"] == "NS"]
    print(f"[football] {len(fixtures)} fixtures")
    if not fixtures:
        return []

    odds = {}
    for lg in sorted({f["league"]["id"] for f in fixtures}):
        page = 1
        while True:
            d = api(FB_BASE, "/odds", league=lg, season=season, date=date_str, page=page)
            for item in d.get("response", []):
                odds[item["fixture"]["id"]] = item["bookmakers"]
            if page >= d.get("paging", {}).get("total", 1) or page >= 3:
                break
            page += 1

    legs, calls = [], 0
    for f in fixtures:
        fid = f["fixture"]["id"]
        if fid not in odds or calls >= MAX_PREDICTION_CALLS:
            continue
        d = api(FB_BASE, "/predictions", fixture=fid)
        calls += 1
        resp = d.get("response")
        if not resp:
            continue
        try:
            pct = {k: float(str(v).strip("%")) / 100
                   for k, v in resp[0]["predictions"]["percent"].items()}
        except (ValueError, KeyError):
            continue
        if not all(k in pct for k in ("home", "draw", "away")):
            continue

        home, away = f["teams"]["home"]["name"], f["teams"]["away"]["name"]
        cands = []
        bm, mw = pick_bookmaker_market(odds[fid], lambda b: b["id"] == 1)  # Match Winner
        if mw:
            if "Home" in mw:
                cands.append((f"{home} to win", mw["Home"], pct["home"], bm))
            if "Away" in mw:
                cands.append((f"{away} to win", mw["Away"], pct["away"], bm))
        bm, dc = pick_bookmaker_market(odds[fid], lambda b: b["id"] == 12)  # Double Chance
        if dc:
            if "Home/Draw" in dc:
                cands.append((f"{home} or draw", dc["Home/Draw"], pct["home"] + pct["draw"], bm))
            if "Draw/Away" in dc:
                cands.append((f"{away} or draw", dc["Draw/Away"], pct["draw"] + pct["away"], bm))

        good = []
        for label, odd, p, bm in cands:
            ev = p * odd - 1
            if LEG_MIN <= odd <= LEG_MAX and p >= MIN_PROB and ev >= FOOTBALL_MIN_EV:
                good.append({"sport": "Football", "match": f"{home} vs {away}",
                             "league": FOOTBALL_LEAGUES[f["league"]["id"]],
                             "kickoff": f["fixture"]["date"], "pick": label,
                             "odd": odd, "prob": p, "ev": ev, "bookmaker": bm,
                             "id": f"fb{fid}", "basis": "model"})
        if good:
            legs.append(max(good, key=lambda x: x["ev"]))
    print(f"[football] {len(legs)} candidate legs")
    return legs


# ---------------- basketball ----------------
def is_moneyline(bet):
    return bet.get("name", "").strip().lower() in ("home/away", "moneyline", "money line")


def basketball_legs(now):
    games = {}
    for d in (now, now + dt.timedelta(days=1)):
        data = api(BB_BASE, "/games", date=d.strftime("%Y-%m-%d"), timezone=TZ)
        for g in data.get("response", []):
            if g["league"]["id"] in BASKETBALL_LEAGUES and g["status"]["short"] == "NS":
                start = dt.datetime.fromisoformat(g["date"])
                hours = (start - now).total_seconds() / 3600
                if 0 < hours <= BASKETBALL_WINDOW_HOURS:
                    games[g["id"]] = g
    print(f"[basketball] {len(games)} games in window")
    if not games:
        return []

    odds = {}
    for lg in sorted({g["league"]["id"] for g in games.values()}):
        season = next(g["league"]["season"] for g in games.values() if g["league"]["id"] == lg)
        page = 1
        while True:
            d = api(BB_BASE, "/odds", league=lg, season=season, page=page)
            for item in d.get("response", []):
                odds[item["game"]["id"]] = item["bookmakers"]
            if page >= d.get("paging", {}).get("total", 1) or page >= 3:
                break
            page += 1

    legs = []
    for gid, g in games.items():
        books = odds.get(gid)
        if not books:
            continue
        # Market consensus: de-vig each bookmaker's moneyline, then average.
        probs_home = []
        for b in books:
            for bet in b.get("bets", []):
                if is_moneyline(bet):
                    try:
                        vals = {v["value"]: float(v["odd"]) for v in bet["values"]}
                        ih, ia = 1 / vals["Home"], 1 / vals["Away"]
                        probs_home.append(ih / (ih + ia))
                    except (KeyError, ValueError, ZeroDivisionError):
                        pass
        if not probs_home:
            continue
        p_home = sum(probs_home) / len(probs_home)
        bm, ml = pick_bookmaker_market(books, is_moneyline)
        if not ml:
            continue

        home, away = g["teams"]["home"]["name"], g["teams"]["away"]["name"]
        good = []
        for label, key, p in ((f"{home} to win", "Home", p_home),
                              (f"{away} to win", "Away", 1 - p_home)):
            if key not in ml:
                continue
            odd = ml[key]
            ev = p * odd - 1
            if LEG_MIN <= odd <= LEG_MAX and p >= MIN_PROB and ev >= BASKETBALL_MIN_EV:
                good.append({"sport": "Basketball", "match": f"{home} vs {away}",
                             "league": BASKETBALL_LEAGUES[g["league"]["id"]],
                             "kickoff": g["date"], "pick": label, "odd": odd,
                             "prob": p, "ev": ev, "bookmaker": bm,
                             "id": f"bb{gid}", "basis": "market"})
        if good:
            legs.append(max(good, key=lambda x: x["ev"]))
    print(f"[basketball] {len(legs)} candidate legs")
    return legs


# ---------------- slip building ----------------
def best_slip(legs):
    best, best_score = None, -9
    for n in (2, 3):
        for combo in itertools.combinations(legs, n):
            total = prob = 1.0
            for leg in combo:
                total *= leg["odd"]
                prob *= leg["prob"]
            if TARGET_MIN <= total <= TARGET_MAX:
                score = prob * total - 1
                if score > best_score:
                    best, best_score = (combo, total, prob), score
    return best


def save_history(entry):
    try:
        with open("history.json") as f:
            hist = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        hist = []
    hist.append(entry)
    with open("history.json", "w") as f:
        json.dump(hist, f, indent=2)


def main():
    sport = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SPORT", "football")).lower()
    if sport not in ("football", "basketball"):
        raise SystemExit("Usage: python daily_slip.py football|basketball")

    now = dt.datetime.now(ZoneInfo(TZ))
    date_str = now.strftime("%Y-%m-%d")
    title = sport.capitalize()

    legs = []
    try:
        legs = football_legs(now) if sport == "football" else basketball_legs(now)
    except Exception as e:
        print(f"{title} failed:", repr(e))
        send(f"{title} slip failed today ({date_str}). Check the GitHub Actions log.")
        return

    pool = sorted(legs, key=lambda x: x["ev"], reverse=True)[:12]
    result = best_slip(pool) if len(pool) >= 2 else None

    if not result:
        send(f"No qualifying {sport} slip today ({date_str}): nothing matched the "
             f"{TARGET_MIN:.2f}-{TARGET_MAX:.2f} odds range with enough confidence. "
             f"Skipping a day is a valid decision.")
        return

    combo, total, prob = result
    lines = [f"<b>{title} slip - {date_str}</b>", ""]
    for i, leg in enumerate(combo, 1):
        basis = "model" if leg["basis"] == "model" else "market-implied"
        lines.append(
            f"{i}. <b>{html.escape(leg['pick'])}</b> @ {leg['odd']:.2f}\n"
            f"   {html.escape(leg['match'])}\n"
            f"   {html.escape(leg['league'])}, {local_time(leg['kickoff'])} EAT\n"
            f"   Win chance {leg['prob']*100:.0f}% ({basis}) | {html.escape(leg['bookmaker'])}"
        )
    lines += [
        "",
        f"<b>Total odds: {total:.2f}</b>",
        f"Estimated hit chance: {prob*100:.0f}%",
        "",
        "Check these odds on betPawa before betting; if the total leaves "
        "2.00-2.50, swap a leg. Bet only what you can afford to lose.",
    ]
    send("\n".join(lines))

    save_history({
        "date": date_str,
        "sport": sport,
        "total_odds": round(total, 2),
        "est_prob": round(prob, 3),
        "legs": [{k: leg[k] for k in ("id", "sport", "match", "pick", "odd", "prob")} for leg in combo],
        "result": "pending",
    })


if __name__ == "__main__":
    main()
