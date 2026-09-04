#!/usr/bin/env python3
"""
Flashscore -> Offensive Pressure HT scanner (v0.1)

Filtro:
- quota pre-match Over 2.5 FT > 1.40 e < 2.00
- indice offensivo p10:
    50% volume tiri
    30% precisione SOT/tiri
    20% conversione gol/SOT (qualità: distanza dalla fascia 25-35%)
- pressione recente: corner fatti combinati p4
- STANDARD: indice >= percentile 70 + corner p4 >= 10
- STRONG:   indice >= percentile 80 + corner p4 >= 11

IMPORTANTE:
I percentile dell'indice devono essere calibrati sul nostro campione storico.
Nel file config.json trovi soglie placeholder. Inseriremo le soglie numeriche
appena le estraiamo dal dataset storico con lo script calibrate_thresholds.py.

Uso consigliato:
    python scanner.py --match-url "https://www.flashscore.com/match/football/.../summary/"
oppure, sperimentale:
    python scanner.py --today

Flashscore non espone una API pubblica documentata. Il codice usa il browser
Playwright e parsing del testo visibile; se il layout cambia, alcune routine
potrebbero richiedere aggiornamento.
"""

from __future__ import annotations
import argparse, asyncio, json, math, re, sqlite3, statistics, sys, time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page, BrowserContext

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DB_PATH = ROOT / CONFIG.get("database", "flashscore_cache.sqlite3")


@dataclass
class TeamMatch:
    url: str
    date: str | None
    team: str
    opponent: str | None
    goals_for: int
    shots: int
    sot: int
    corners: int


@dataclass
class CandidateResult:
    home: str
    away: str
    match_url: str
    over25_odds: float | None
    home_games: int
    away_games: int
    shots_combined_p10: float | None
    precision_pct: float | None
    conversion_pct: float | None
    offensive_score: float | None
    corners_combined_p4: float | None
    verdict: str
    reason: str


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS team_match_stats(
            match_url TEXT NOT NULL,
            team TEXT NOT NULL,
            date TEXT,
            opponent TEXT,
            goals_for INTEGER NOT NULL,
            shots INTEGER NOT NULL,
            sot INTEGER NOT NULL,
            corners INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY(match_url, team)
        )
    """)
    con.commit()
    con.close()


def num(s: str) -> Optional[float]:
    if not s:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)", s.replace("\xa0"," "))
    return float(m.group(1).replace(",", ".")) if m else None


async def body_text(page: Page) -> str:
    return await page.locator("body").inner_text(timeout=15000)


async def dismiss_cookies(page: Page):
    for label in ["Accept all", "I agree", "Accetta tutto", "Accetto", "Agree"]:
        try:
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            if await btn.count():
                await btn.first.click(timeout=1500)
                return
        except:
            pass


async def safe_goto(page: Page, url: str, wait_ms=900):
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await dismiss_cookies(page)
    await page.wait_for_timeout(wait_ms)


def parse_teams_from_text(text: str) -> tuple[str,str]:
    # In match page the first lines normally contain date, home, score/status, away.
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    # Prefer title-like area before "Match"/"Report"/"Odds".
    cut = lines[:30]
    # remove obvious boilerplate
    bad = re.compile(r"^(Flashscore|Football|Match|Report|Odds|H2H|Standings|Summary|Stats|Lineups|Commentary|Finished|Scheduled|Postponed)$", re.I)
    candidates = [x for x in cut if not bad.match(x) and not re.match(r"^\d{1,2}[./]\d{1,2}", x)
                  and not re.match(r"^\d+[-:]\d+$", x) and len(x) > 1]
    # Best-effort: use document title if possible elsewhere; fallback here.
    if len(candidates) >= 2:
        return candidates[0], candidates[1]
    return "HOME", "AWAY"


async def parse_match_header(page: Page) -> tuple[str,str]:
    title = await page.title()
    # Typical: "Team A v Team B 31/08/2026, ... - Flashscore.com"
    m = re.search(r"^(.*?)\s+v\s+(.*?)\s+\d{1,2}/\d{1,2}/\d{4}", title)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return parse_teams_from_text(await body_text(page))


async def get_over25_odds(page: Page, match_url: str) -> Optional[float]:
    # Try several current/legacy URL shapes. We parse visible text and look for
    # "Over 2.5" / "Over 2,5" followed by decimal odds.
    base = re.sub(r"/(summary|stats|odds).*$", "/", match_url.rstrip("/") + "/")
    variants = [
        base + "odds/over-under/full-time/",
        base + "odds/",
        base,
    ]
    for url in variants:
        try:
            await safe_goto(page, url)
            text = await body_text(page)
            patterns = [
                r"Over\s*2[.,]5[^\n]{0,80}?(\d[.,]\d{2,3})",
                r"2[.,]5[^\n]{0,80}?Over[^\n]{0,40}?(\d[.,]\d{2,3})",
            ]
            vals = []
            for p in patterns:
                vals += [float(x.replace(",", ".")) for x in re.findall(p, text, re.I)]
            vals = [x for x in vals if 1.01 <= x <= 10]
            if vals:
                # Prefer median if several bookmakers are displayed.
                return statistics.median(vals)
        except:
            continue
    return None


async def extract_team_links(page: Page) -> dict[str,str]:
    # Flashscore match pages contain links to both team pages.
    links = await page.locator('a[href*="/team/"]').evaluate_all(
        """els => els.map(a => ({text:(a.innerText||a.textContent||"").trim(), href:a.href}))"""
    )
    out = {}
    for x in links:
        if x["text"] and x["href"]:
            out.setdefault(x["text"], x["href"])
    return out


async def collect_recent_match_links(page: Page, team_url: str, limit=14) -> list[str]:
    # Team pages usually expose recent results under /results/.
    urls = [team_url.rstrip("/") + "/results/", team_url]
    found = []
    for url in urls:
        try:
            await safe_goto(page, url, 1200)
            # click "Show more" a few times if present
            for _ in range(2):
                try:
                    btn = page.get_by_text(re.compile(r"Show more|Mostra altro", re.I))
                    if await btn.count():
                        await btn.last.click(timeout=1200)
                        await page.wait_for_timeout(600)
                except:
                    break
            hrefs = await page.locator('a[href*="/match/football/"]').evaluate_all(
                """els => els.map(a => a.href)"""
            )
            for h in hrefs:
                if h not in found:
                    found.append(h)
            if len(found) >= limit:
                break
        except:
            pass
    return found[:limit]


def parse_stat_pair(text: str, labels: list[str]) -> Optional[tuple[int,int]]:
    # Flashscore body text often renders as:
    # 14
    # Total shots
    # 8
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    labelset = {x.lower() for x in labels}
    for i, line in enumerate(lines):
        if line.lower() in labelset:
            # inspect nearby numeric lines
            left = None; right = None
            for j in range(i-1, max(-1,i-4), -1):
                if re.fullmatch(r"\d+", lines[j]):
                    left = int(lines[j]); break
            for j in range(i+1, min(len(lines),i+4)):
                if re.fullmatch(r"\d+", lines[j]):
                    right = int(lines[j]); break
            if left is not None and right is not None:
                return left, right
    return None


async def parse_finished_match_stats(page: Page, url: str, target_team: str) -> Optional[TeamMatch]:
    stats_url = re.sub(r"/(summary|stats|odds).*$", "/", url.rstrip("/") + "/") + "summary/stats/"
    try:
        await safe_goto(page, stats_url, 700)
        text = await body_text(page)
        home, away = await parse_match_header(page)

        shots_pair = parse_stat_pair(text, ["Total shots","Goal Attempts","Tiri totali"])
        sot_pair = parse_stat_pair(text, ["Shots on target","Shots on goal","Tiri in porta"])
        corners_pair = parse_stat_pair(text, ["Corner kicks","Corner Kicks","Calci d'angolo"])
        if not (shots_pair and sot_pair and corners_pair):
            return None

        # score from title/body
        score = None
        m = re.search(r"\b(\d+)\s*[-:]\s*(\d+)\b", text[:1200])
        if m:
            score = (int(m.group(1)), int(m.group(2)))
        if not score:
            return None

        norm = lambda s: re.sub(r"\W+","",s.lower())
        is_home = norm(target_team) in norm(home) or norm(home) in norm(target_team)
        is_away = norm(target_team) in norm(away) or norm(away) in norm(target_team)
        if not is_home and not is_away:
            return None

        idx = 0 if is_home else 1
        opp = away if idx == 0 else home
        return TeamMatch(
            url=url, date=None, team=target_team, opponent=opp,
            goals_for=score[idx],
            shots=shots_pair[idx], sot=sot_pair[idx], corners=corners_pair[idx]
        )
    except Exception:
        return None


def db_get(team: str, url: str) -> Optional[TeamMatch]:
    con = sqlite3.connect(DB_PATH)
    row = con.execute("""SELECT match_url,date,team,opponent,goals_for,shots,sot,corners
                         FROM team_match_stats WHERE match_url=? AND team=?""",(url,team)).fetchone()
    con.close()
    if not row: return None
    return TeamMatch(url=row[0],date=row[1],team=row[2],opponent=row[3],
                     goals_for=row[4],shots=row[5],sot=row[6],corners=row[7])


def db_put(m: TeamMatch):
    con = sqlite3.connect(DB_PATH)
    con.execute("""INSERT OR REPLACE INTO team_match_stats
        (match_url,team,date,opponent,goals_for,shots,sot,corners,fetched_at)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (m.url,m.team,m.date,m.opponent,m.goals_for,m.shots,m.sot,m.corners,
         datetime.now(timezone.utc).isoformat()))
    con.commit(); con.close()


async def team_history(context: BrowserContext, team: str, team_url: str, n=10) -> list[TeamMatch]:
    page = await context.new_page()
    links = await collect_recent_match_links(page, team_url, limit=max(14,n+4))
    out = []
    for url in links:
        cached = db_get(team,url)
        if cached:
            out.append(cached)
        else:
            m = await parse_finished_match_stats(page,url,team)
            if m:
                db_put(m); out.append(m)
        if len(out) >= n:
            break
    await page.close()
    return out


def offensive_components(home_hist: list[TeamMatch], away_hist: list[TeamMatch]):
    h10, a10 = home_hist[:10], away_hist[:10]
    if len(h10)<10 or len(a10)<10:
        return None
    # Average team shots, then sum: same scale as previous backtest "combined shots".
    h_sh = statistics.mean(x.shots for x in h10)
    a_sh = statistics.mean(x.shots for x in a10)
    shots_comb = h_sh + a_sh

    total_shots = sum(x.shots for x in h10+a10)
    total_sot = sum(x.sot for x in h10+a10)
    total_goals = sum(x.goals_for for x in h10+a10)
    precision = 100*total_sot/total_shots if total_shots else 0
    conversion = 100*total_goals/total_sot if total_sot else 0

    # Score is normalized using config calibration.
    cal = CONFIG["calibration"]
    def z(x, mean, sd): return (x-mean)/sd if sd else 0
    # Conversion "quality": best around 30%; penalize distance from 30.
    conv_quality = -abs(conversion - cal.get("conversion_target",30.0))
    score = (
        .50*z(shots_comb,cal["shots_mean"],cal["shots_sd"]) +
        .30*z(precision,cal["precision_mean"],cal["precision_sd"]) +
        .20*z(conv_quality,cal["conv_quality_mean"],cal["conv_quality_sd"])
    )
    corners_p4 = statistics.mean(x.corners for x in home_hist[:4]) + statistics.mean(x.corners for x in away_hist[:4])
    return shots_comb, precision, conversion, score, corners_p4


def classify(score: float, corners: float) -> tuple[str,str]:
    cal = CONFIG["calibration"]
    p70, p80 = cal["score_p70"], cal["score_p80"]
    if score >= p80 and corners >= 11:
        return "STRONG", "indice p10 Top 20% + corner p4 ≥11"
    if score >= p70 and corners >= 10:
        return "STANDARD", "indice p10 Top 30% + corner p4 ≥10"
    return "NO BET", "soglie Offensive Pressure HT non raggiunte"


async def analyse_match(context: BrowserContext, url: str) -> CandidateResult:
    page = await context.new_page()
    await safe_goto(page,url)
    home, away = await parse_match_header(page)
    team_links = await extract_team_links(page)
    odds = await get_over25_odds(page,url)

    if odds is None:
        await page.close()
        return CandidateResult(home,away,url,None,0,0,None,None,None,None,None,
                               "CHECK","quota O2,5 non letta automaticamente")
    if not (CONFIG["over25_min"] < odds < CONFIG["over25_max"]):
        await page.close()
        return CandidateResult(home,away,url,odds,0,0,None,None,None,None,None,
                               "NO BET","quota O2,5 fuori 1.40–2.00")

    # Match team names to links.
    def best_link(name):
        nn = re.sub(r"\W+","",name.lower())
        scored=[]
        for txt,href in team_links.items():
            tt=re.sub(r"\W+","",txt.lower())
            score = len(set(nn) & set(tt)) + (100 if nn in tt or tt in nn else 0)
            scored.append((score,href,txt))
        return max(scored, default=(0,None,None))[1]

    hurl, aurl = best_link(home), best_link(away)
    if not hurl or not aurl:
        await page.close()
        return CandidateResult(home,away,url,odds,0,0,None,None,None,None,None,
                               "CHECK","link squadra non trovato")

    hh = await team_history(context,home,hurl,10)
    ah = await team_history(context,away,aurl,10)
    comp = offensive_components(hh,ah)
    if not comp:
        await page.close()
        return CandidateResult(home,away,url,odds,len(hh),len(ah),None,None,None,None,None,
                               "CHECK","meno di 10 precedenti con statistiche complete")

    sh,prec,conv,score,corn = comp
    verdict, reason = classify(score,corn)
    await page.close()
    return CandidateResult(home,away,url,odds,len(hh),len(ah),sh,prec,conv,score,corn,verdict,reason)


async def today_links(page: Page) -> list[str]:
    await safe_goto(page, "https://www.flashscore.com/football/", 1800)
    hrefs = await page.locator('a[href*="/match/football/"]').evaluate_all("els => els.map(a=>a.href)")
    out=[]
    for h in hrefs:
        if h not in out: out.append(h)
    return out


def print_result(r: CandidateResult):
    print("\n" + "="*76)
    print(f"{r.home} - {r.away}")
    print(f"O2.5: {r.over25_odds if r.over25_odds is not None else 'n/d'}")
    if r.offensive_score is not None:
        print(f"Tiri combinati p10: {r.shots_combined_p10:.2f}")
        print(f"Precisione SOT:       {r.precision_pct:.2f}%")
        print(f"Conversione:          {r.conversion_pct:.2f}%")
        print(f"Indice offensivo:     {r.offensive_score:.3f}")
        print(f"Corner combinati p4:  {r.corners_combined_p4:.2f}")
    print(f"VERDETTO: {r.verdict} — {r.reason}")


async def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--match-url")
    ap.add_argument("--today", action="store_true")
    ap.add_argument("--headful", action="store_true", help="mostra Chrome")
    ap.add_argument("--max-matches", type=int, default=80)
    ap.add_argument("--csv", default="scan_results.csv")
    args=ap.parse_args()

    if not args.match_url and not args.today:
        ap.error("usa --match-url URL oppure --today")

    init_db()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not args.headful)
        context = await browser.new_context(
            locale="en-US",
            timezone_id="Europe/Rome",
            viewport={"width":1440,"height":1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
        )
        page=await context.new_page()
        urls=[args.match_url] if args.match_url else (await today_links(page))[:args.max_matches]
        await page.close()

        results=[]
        for i,u in enumerate(urls,1):
            print(f"[{i}/{len(urls)}] {u}", flush=True)
            try:
                r=await analyse_match(context,u)
            except Exception as e:
                r=CandidateResult("?","?",u,None,0,0,None,None,None,None,None,"ERROR",str(e))
            results.append(r)
            print_result(r)

        import csv
        with open(args.csv,"w",newline="",encoding="utf-8-sig") as f:
            w=csv.DictWriter(f,fieldnames=list(asdict(results[0]).keys()) if results else CandidateResult.__annotations__.keys())
            w.writeheader()
            for r in results: w.writerow(asdict(r))
        print(f"\nSalvato: {args.csv}")
        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
