#!/usr/bin/env python3
"""
ESPN Fantasy Football data fetcher — GitHub Actions version.

Reads SWID and ESPN_S2 from environment variables (set as GitHub Secrets)
rather than hardcoding them, since this file lives in a repo.

Saves league_data.json to the repo root, which GitHub Pages then serves
alongside index.html.
"""

import json
import os
import requests

# ---- CONFIG: these two are not sensitive, safe to commit ----
LEAGUE_ID = 490456
SEASON = 2026
# ---------------------------------------------------------------

SWID = os.environ.get("ESPN_SWID", "")
ESPN_S2 = os.environ.get("ESPN_S2", "")

if not SWID or not ESPN_S2:
    raise SystemExit("Missing ESPN_SWID or ESPN_S2 environment variables. "
                      "Set them as GitHub Secrets in repo Settings > Secrets and variables > Actions.")

BASE_URL = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
COOKIES = {"SWID": SWID, "espn_s2": ESPN_S2}
HEADERS = {"User-Agent": "Mozilla/5.0 (fantasy-tracker-action)"}
OUTPUT_FILE = "league_data.json"


def get(view_params, extra_params=None):
    params = {"view": view_params}
    if extra_params:
        params.update(extra_params)
    resp = requests.get(BASE_URL, cookies=COOKIES, headers=HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def main():
    print(f"Fetching league {LEAGUE_ID}, season {SEASON}...")
    core = get(["mSettings", "mTeam", "mMatchup", "mStandings"])

    league_name = core.get("settings", {}).get("name", "League")
    teams = core.get("teams", [])
    schedule = core.get("schedule", [])
    status = core.get("status", {})
    current_week = status.get("latestScoringPeriod", 1)

    print(f"League: {league_name} | Teams: {len(teams)} | Week: {current_week}")

    weekly_boxscores = {}
    for wk in range(1, current_week + 1):
        try:
            wk_data = get(["mBoxscore", "mMatchupScore"], extra_params={"scoringPeriodId": wk})
            wk_schedule = wk_data.get("schedule", [])
            weekly_boxscores[str(wk)] = wk_schedule
            print(f"  Week {wk}: {len(wk_schedule)} matchup(s) in box score data")
        except requests.HTTPError as e:
            print(f"  Skipped week {wk}: {e}")

    output = {
        "leagueId": LEAGUE_ID,
        "season": SEASON,
        "leagueName": league_name,
        "teams": teams,
        "schedule": schedule,
        "currentWeek": current_week,
        "weeklyBoxscores": weekly_boxscores,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f)

    print(f"Saved {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
