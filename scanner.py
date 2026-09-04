#!/usr/bin/env python3
"""Offensive Pressure HT v0.4 — HTTP engine, no Chromium/Playwright.
Uses Flashscore's internal text feeds. Undocumented endpoints may change.
"""
from __future__ import annotations
import json, re, sqlite3, statistics, time, unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
import requests

ROOT=Path(__file__).resolve().parent
CONFIG=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
DB_PATH=ROOT/CONFIG.get('database','flashscore_cache.sqlite3')
BASE='https://local-global.flashscore.ninja/2/x/feed/'
TODAY=BASE+'f_1_0_3_en_1'
ODDS='https://global.ds.lsapp.eu/odds/pq_graphql'
HEADERS={
 'x-fsign':'SW9D1eZo','User-Agent':'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/126 Safari/537.36',
 'Accept':'*/*','Accept-Language':'en-GB,en;q=0.9','Referer':'https://www.flashscore.com/','Origin':'https://www.flashscore.com'
}
S=requests.Session(); S.headers.update(HEADERS)

@dataclass
class TeamMatch:
    url:str; date:str|None; team:str; opponent:str|None; goals_for:int; shots:int; sot:int; corners:int
@dataclass
class CandidateResult:
    home:str; away:str; match_url:str; over25_odds:float|None; home_games:int; away_games:int
    shots_combined_p10:float|None; precision_pct:float|None; conversion_pct:float|None
    offensive_score:float|None; corners_combined_p4:float|None; verdict:str; reason:str

def init_db():
    con=sqlite3.connect(DB_PATH)
    con.execute('''CREATE TABLE IF NOT EXISTS team_match_stats(match_url TEXT NOT NULL,team TEXT NOT NULL,date TEXT,opponent TEXT,goals_for INTEGER NOT NULL,shots INTEGER NOT NULL,sot INTEGER NOT NULL,corners INTEGER NOT NULL,fetched_at TEXT NOT NULL,PRIMARY KEY(match_url,team))''')
    con.commit(); con.close()

def _get(url, *, params=None, timeout=15, tries=2):
    err=None
    for i in range(tries):
        try:
            r=S.get(url,params=params,timeout=timeout)
            r.raise_for_status(); return r
        except Exception as e:
            err=e; time.sleep(.35*(i+1))
    raise RuntimeError(f'Flashscore HTTP non raggiungibile: {err}')

def _fields(section:str):
    out={}
    for p in section.split('¬'):
        if '÷' in p:
            k,_,v=p.partition('÷'); out[k.lstrip('~')]=v
        elif '·' in p:
            k,_,v=p.partition('·'); out[k.lstrip('~')]=v
    return out

def parse_records(raw:str):
    return [_fields(x) for x in raw.split('~') if x.strip()]

def norm(s):
    s=unicodedata.normalize('NFKD',s or '').encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',s)

def same_team(a,b):
    a,b=norm(a),norm(b)
    return bool(a and b and (a==b or (len(a)>4 and a in b) or (len(b)>4 and b in a)))

def match_id(value:str)->str:
    value=(value or '').strip()
    m=re.search(r'/match/(?:football/)?(?:[^/]+/)*([A-Za-z0-9]{8})(?:/|$)',value)
    if m:return m.group(1)
    m=re.search(r'([A-Za-z0-9]{8})',value)
    if m:return m.group(1)
    raise ValueError('ID partita Flashscore non riconosciuto')

def today_matches():
    raw=_get(TODAY).text
    out=[]
    for d in parse_records(raw):
        if not d.get('AA') or not d.get('AE') or not d.get('AF'): continue
        # football feed can contain scheduled/live/finished; keep today entries, prioritize scheduled/live
        mid=d['AA']; home=d['AE']; away=d['AF']
        out.append({'id':mid,'home':home,'away':away,'ts':d.get('AD'),'status':d.get('AB'),
                    'url':f'https://www.flashscore.com/match/{mid}/'})
    # remove duplicate ids while preserving order
    seen=set(); clean=[]
    for x in out:
        if x['id'] not in seen: seen.add(x['id']); clean.append(x)
    return clean

def current_meta(mid:str):
    for m in today_matches():
        if m['id']==mid:return m
    # general detail endpoint frequently contains AE/AF too
    try:
        rec=parse_records(_get(BASE+'dc_1_'+mid).text)
        for d in rec:
            if d.get('AE') and d.get('AF'):
                return {'id':mid,'home':d['AE'],'away':d['AF'],'url':f'https://www.flashscore.com/match/{mid}/'}
    except: pass
    return {'id':mid,'home':'HOME','away':'AWAY','url':f'https://www.flashscore.com/match/{mid}/'}

def h2h_matches(mid:str):
    raw=_get(BASE+'df_hh_1_'+mid).text
    out=[]
    for d in parse_records(raw):
        if d.get('AA') and d.get('AE') and d.get('AF'):
            try: gh=int(float(d.get('AG','0') or 0)); ga=int(float(d.get('AH','0') or 0))
            except: gh=ga=0
            out.append({'id':d['AA'],'home':d['AE'],'away':d['AF'],'gh':gh,'ga':ga,'ts':d.get('AD'),'status':d.get('AB')})
    seen=set(); res=[]
    for x in out:
        if x['id']!=mid and x['id'] not in seen:
            seen.add(x['id']);res.append(x)
    return res

def parse_stats(mid:str):
    raw=_get(BASE+'df_st_1_'+mid).text
    found={}
    aliases={'total shots':'shots','goal attempts':'shots','shots on target':'sot','shots on goal':'sot','corner kicks':'corners'}
    for d in parse_records(raw):
        label=(d.get('SG') or d.get('SD') or '').strip().lower()
        key=aliases.get(label)
        if not key or key in found: continue
        try:
            h=float(re.sub(r'[^0-9.]','',d.get('SH','')) or 'nan'); a=float(re.sub(r'[^0-9.]','',d.get('SI','')) or 'nan')
            if h==h and a==a: found[key]=(int(round(h)),int(round(a)))
        except: pass
    return found if {'shots','sot','corners'}<=set(found) else None

def db_get(team,url):
    con=sqlite3.connect(DB_PATH); row=con.execute('SELECT match_url,date,team,opponent,goals_for,shots,sot,corners FROM team_match_stats WHERE match_url=? AND team=?',(url,team)).fetchone(); con.close()
    return TeamMatch(*row) if row else None

def db_put(m):
    con=sqlite3.connect(DB_PATH); con.execute('INSERT OR REPLACE INTO team_match_stats VALUES(?,?,?,?,?,?,?,?,?)',(m.url,m.team,m.date,m.opponent,m.goals_for,m.shots,m.sot,m.corners,datetime.now(timezone.utc).isoformat()));con.commit();con.close()

def history_for(team:str, matches:list[dict], n=10):
    out=[]
    candidates=[m for m in matches if same_team(team,m['home']) or same_team(team,m['away'])]
    candidates.sort(key=lambda x:int(x.get('ts') or 0), reverse=True)
    for m in candidates:
        url=f'https://www.flashscore.com/match/{m["id"]}/'
        cached=db_get(team,url)
        if cached: out.append(cached)
        else:
            st=parse_stats(m['id'])
            if not st: continue
            is_home=same_team(team,m['home']); idx=0 if is_home else 1
            tm=TeamMatch(url,None,team,m['away'] if is_home else m['home'],m['gh'] if is_home else m['ga'],st['shots'][idx],st['sot'][idx],st['corners'][idx])
            db_put(tm);out.append(tm)
        if len(out)>=n: break
        time.sleep(.10)
    return out

def _walk(obj):
    if isinstance(obj,dict):
        yield obj
        for v in obj.values():yield from _walk(v)
    elif isinstance(obj,list):
        for v in obj:yield from _walk(v)

def over25_odds(mid:str):
    # Current odds query used by Flashscore web clients. Heuristics tolerate schema naming changes.
    params={'_hash':'oce','eventId':mid,'projectId':'2','geoIpCode':'IT'}
    try:data=_get(ODDS,params=params,timeout=12).json()
    except:return None
    vals=[]
    for d in _walk(data):
        text=' '.join(str(d.get(k,'')) for k in ('bettingType','bettingScope','name','label','handicap','line','specifier')).upper()
        odds=d.get('odds')
        if not isinstance(odds,list): continue
        # identify full-time 2.5 total markets from any textual metadata nearby
        if '2.5' not in text and '2,5' not in text: continue
        if 'FULL_TIME' not in text and 'FULL TIME' not in text and d.get('bettingScope') not in (None,'FULL_TIME'): continue
        nums=[]
        for o in odds:
            if not isinstance(o,dict):continue
            try:v=float(o.get('value'))
            except:continue
            label=' '.join(str(o.get(k,'')) for k in ('name','label','type','outcome')).upper()
            nums.append((label,v))
        overs=[v for label,v in nums if 'OVER' in label]
        if overs: vals.extend(overs)
        elif len(nums)==2 and any(t in text for t in ('OVER_UNDER','TOTAL','GOALS')):
            vals.append(nums[0][1])
    vals=[v for v in vals if 1.01<=v<=10]
    return statistics.median(vals) if vals else None

def offensive_components(h,a):
    if len(h)<10 or len(a)<10:return None
    allm=h[:10]+a[:10]
    shots=statistics.mean(x.shots for x in h[:10])+statistics.mean(x.shots for x in a[:10])
    tsh=sum(x.shots for x in allm); tsot=sum(x.sot for x in allm); tg=sum(x.goals_for for x in allm)
    prec=100*tsot/tsh if tsh else 0; conv=100*tg/tsot if tsot else 0
    cal=CONFIG['calibration']; z=lambda x,m,s:(x-m)/s if s else 0
    cq=-abs(conv-cal.get('conversion_target',30.0))
    score=.50*z(shots,cal['shots_mean'],cal['shots_sd'])+.30*z(prec,cal['precision_mean'],cal['precision_sd'])+.20*z(cq,cal['conv_quality_mean'],cal['conv_quality_sd'])
    corners=statistics.mean(x.corners for x in h[:4])+statistics.mean(x.corners for x in a[:4])
    return shots,prec,conv,score,corners

def classify(score,corners):
    cal=CONFIG['calibration']
    if score>=cal['score_p80'] and corners>=11:return 'STRONG','indice p10 Top 20% + corner p4 ≥11'
    if score>=cal['score_p70'] and corners>=10:return 'STANDARD','indice p10 Top 30% + corner p4 ≥10'
    return 'NO BET','soglie Offensive Pressure HT non raggiunte'

def analyse_match(url_or_id:str, feed_match=None):
    init_db(); mid=match_id(url_or_id) if len(url_or_id)!=8 else url_or_id
    meta=feed_match or current_meta(mid); home,away=meta.get('home','HOME'),meta.get('away','AWAY'); url=meta.get('url') or f'https://www.flashscore.com/match/{mid}/'
    odds=over25_odds(mid)
    if odds is None:
        return CandidateResult(home,away,url,None,0,0,None,None,None,None,None,'CHECK','quota O2,5 non letta dal feed HTTP')
    if not (CONFIG['over25_min']<odds<CONFIG['over25_max']):
        return CandidateResult(home,away,url,odds,0,0,None,None,None,None,None,'NO BET','quota O2,5 fuori fascia 1.40–2.00')
    hist=h2h_matches(mid)
    hh=history_for(home,hist,10); ah=history_for(away,hist,10)
    comp=offensive_components(hh,ah)
    if not comp:
        return CandidateResult(home,away,url,odds,len(hh),len(ah),None,None,None,None,None,'CHECK',f'storico insufficiente: {len(hh)}/10 e {len(ah)}/10')
    shots,prec,conv,score,corners=comp; verdict,reason=classify(score,corners)
    return CandidateResult(home,away,url,odds,len(hh),len(ah),round(shots,2),round(prec,2),round(conv,2),round(score,3),round(corners,2),verdict,reason)

if __name__=='__main__':
    print(f'{len(today_matches())} partite nel feed di oggi')
