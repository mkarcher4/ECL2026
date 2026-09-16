#!/usr/bin/env python3
"""
ESPN Fantasy Football data fetcher — GitHub Actions version.

Reads SWID and ESPN_S2 from environment variables (set as GitHub Secrets)
rather than hardcoding them, since this file lives in a repo.

Saves league_data.json to the repo root, which GitHub Pages then serves
alongside index.html.
"""

import datetime
import json
import os
import requests

# ---- CONFIG: these two are not sensitive, safe to commit ----
LEAGUE_ID = 490456
# ---------------------------------------------------------------

SWID = os.environ.get("ESPN_SWID", "")
ESPN_S2 = os.environ.get("ESPN_S2", "")

if not SWID or not ESPN_S2:
    raise SystemExit("Missing ESPN_SWID or ESPN_S2 environment variables. "
                      "Set them as GitHub Secrets in repo Settings > Secrets and variables > Actions.")

COOKIES = {"SWID": SWID, "espn_s2": ESPN_S2}
HEADERS = {"User-Agent": "Mozilla/5.0 (fantasy-tracker-action)"}
OUTPUT_FILE = "league_data.json"


def base_url(season):
    return f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{LEAGUE_ID}"


def get(season, view_params, extra_params=None):
    params = {"view": view_params}
    if extra_params:
        params.update(extra_params)
    resp = requests.get(base_url(season), cookies=COOKIES, headers=HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def discover_available_seasons():
    """
    Finds every season this league has existed under LEAGUE_ID, instead of
    hardcoding a year list. Uses ESPN's league-history endpoint, which
    returns one entry per season the league has existed.

    Falls back to just the current calendar year if discovery fails for any
    reason (endpoint shape changes, network issue, brand-new league with no
    history yet), so the script never breaks even without this feature
    working -- it only adds years on top of what always worked before.

    Note: this endpoint pattern is confirmed for leagues from 2018 onward.
    ESPN used a different API structure before that, so a league with
    history older than 2018 may need additional handling -- if very old
    seasons don't show up, that's the likely reason.
    """
    url = f"https://fantasy.espn.com/apis/v3/games/ffl/leagueHistory/{LEAGUE_ID}"
    current_year = datetime.date.today().year

    try:
        resp = requests.get(url, cookies=COOKIES, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Could not discover league history ({e}); falling back to current year only.")
        return [current_year]

    entries = data if isinstance(data, list) else [data]
    seasons = set()
    for entry in entries:
        sid = entry.get("seasonId") or entry.get("season")
        if sid:
            seasons.add(int(sid))

    if not seasons:
        print("League history returned no seasons; falling back to current year only.")
        return [current_year]

    # The current in-progress season doesn't always show up in "history"
    # right away -- make sure it's included regardless.
    seasons.add(current_year)

    return sorted(seasons, reverse=True)


def fetch_season_player_stats(season):
    """
    Pulls full-season point totals for every rosterable player in the league,
    regardless of whether they are currently on any team's roster. This is
    the only way to correctly grade a drafted player who was later dropped
    and never picked up again -- the per-week box score data only shows
    players while they're actually rostered somewhere, so a player who
    disappears from every roster for the rest of the season would otherwise
    show an incomplete point total.

    Uses ESPN's full player-pool endpoint (view=kona_player_info) with the
    x-fantasy-filter header, which returns every draftable player league-wide
    along with a season-total stat line (scoringPeriodId 0, statSourceId 0).

    Returns {} on any failure so the caller can fall back to the box-score
    -derived totals instead -- this is a supplementary data source, not a
    required one, and its absence should never break the rest of the fetch.
    """
    filters = {
        "players": {
            "limit": 3000,
            "sortAppliedStatTotal": {
                "sortAsc": False,
                "sortPriority": 1,
                "value": {"seasonId": season}
            }
        }
    }
    headers = dict(HEADERS)
    headers["x-fantasy-filter"] = json.dumps(filters)

    try:
        resp = requests.get(base_url(season), cookies=COOKIES, headers=headers,
                             params={"view": "kona_player_info"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  Could not fetch full player pool stats (falling back to box scores): {e}")
        return {}

    players = data.get("players", [])
    result = {}
    for entry in players:
        p = entry.get("player", {})
        pid = p.get("id")
        if pid is None:
            continue
        season_total = None
        for stat in p.get("stats", []):
            if (stat.get("seasonId") == season and stat.get("scoringPeriodId") == 0
                    and stat.get("statSourceId") == 0):
                season_total = stat.get("appliedTotal")
                break
        result[str(pid)] = {
            "name": p.get("fullName", "Unknown Player"),
            "defaultPositionId": p.get("defaultPositionId"),
            "eligibleSlots": p.get("eligibleSlots", []),
            "points": season_total
        }

    print(f"  Full player pool stats fetched: {len(result)} players")
    return result


def fetch_season_data(season):
    """Fetches one full season's worth of league data (the same shape the
    app has always expected) and returns it as a dict."""
    print(f"Fetching league {LEAGUE_ID}, season {season}...")
    core = get(season, ["mSettings", "mTeam", "mMatchup", "mStandings", "mDraftDetail"])

    league_name = core.get("settings", {}).get("name", "League")
    teams = core.get("teams", [])
    schedule = core.get("schedule", [])
    status = core.get("status", {})
    current_week = status.get("latestScoringPeriod", 1)
    draft_picks = core.get("draftDetail", {}).get("picks", [])
    print(f"Draft picks fetched: {len(draft_picks)}")

    season_player_stats = fetch_season_player_stats(season)

    print(f"League: {league_name} | Teams: {len(teams)} | Week: {current_week}")

    weekly_boxscores = {}
    for wk in range(1, current_week + 1):
        try:
            wk_data = get(season, ["mBoxscore", "mMatchupScore"], extra_params={"scoringPeriodId": wk})
            wk_schedule = wk_data.get("schedule", [])
            weekly_boxscores[str(wk)] = wk_schedule
            print(f"  Week {wk}: {len(wk_schedule)} matchup(s) in box score data")
        except requests.HTTPError as e:
            print(f"  Skipped week {wk}: {e}")

    return {
        "leagueId": LEAGUE_ID,
        "season": season,
        "leagueName": league_name,
        "teams": teams,
        "schedule": schedule,
        "currentWeek": current_week,
        "weeklyBoxscores": weekly_boxscores,
        "draftPicks": draft_picks,
        "seasonPlayerStats": season_player_stats,
    }


def main():
    seasons = discover_available_seasons()
    print(f"Seasons discovered for league {LEAGUE_ID}: {seasons}")

    seasons_data = {}
    for season in seasons:
        try:
            seasons_data[str(season)] = fetch_season_data(season)
        except requests.HTTPError as e:
            print(f"Skipped season {season} entirely: {e}")

    if not seasons_data:
        raise SystemExit("No seasons were fetched successfully -- nothing to save.")

    output = {
        "availableSeasons": sorted((int(s) for s in seasons_data.keys()), reverse=True),
        "seasons": seasons_data,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f)

    print(f"Saved {OUTPUT_FILE} with {len(seasons_data)} season(s): {list(seasons_data.keys())}")


if __name__ == "__main__":
    main()
