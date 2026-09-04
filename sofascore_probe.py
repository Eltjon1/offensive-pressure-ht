#!/usr/bin/env python3
import json, sys, time
from datetime import date
import requests

BASE="https://www.sofascore.com/api/v1"
ALT="https://api.sofascore.com/api/v1"
HEADERS={"User-Agent":"Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/126 Safari/537.36","Accept":"application/json"}

def get(path):
    last=None
    for base in (BASE,ALT):
        try:
            r=requests.get(base+path,headers=HEADERS,timeout=15)
            if r.status_code==200:
                return r.json(), r.status_code, base
            last=(None,r.status_code,base,r.text[:200])
        except Exception as e:
            last=(None,None,base,str(e))
    return last

def stat_keys(data):
    keys=set()
    for period in data.get("statistics",[]):
        if period.get("period")!="ALL": continue
        for g in period.get("groups",[]):
            for x in g.get("statisticsItems",[]):
                keys.add((x.get("key"),x.get("name")))
    return sorted(keys,key=lambda x:str(x))

def main():
    d=sys.argv[1] if len(sys.argv)>1 else date.today().isoformat()
    sched=get(f"/sport/football/scheduled-events/{d}")
    if not sched or sched[0] is None:
        print(json.dumps({"ok":False,"stage":"schedule","detail":sched},ensure_ascii=False,indent=2)); return
    events=sched[0].get("events",[])[:20]
    report={"ok":True,"date":d,"events_found":len(sched[0].get("events",[])),"tested":[]}
    for ev in events:
        eid=ev.get("id"); home=ev.get("homeTeam",{}); away=ev.get("awayTeam",{})
        row={"id":eid,"match":f"{home.get('name')} - {away.get('name')}",
             "homeTeamId":home.get("id"),"awayTeamId":away.get("id")}
        odds=get(f"/event/{eid}/odds/1/all")
        row["odds_status"]=odds[1] if odds else None
        if odds and odds[0]:
            raw=odds[0]
            row["odds_top_keys"]=list(raw.keys())[:20]
            txt=json.dumps(raw,ensure_ascii=False)
            row["has_2_5_text"]="2.5" in txt
            row["odds_preview"]=txt[:1200]
        # Team previous events: probe several known SofaScore patterns.
        for side,tid in (("home",home.get("id")),("away",away.get("id"))):
            if not tid: continue
            candidates=[
                f"/team/{tid}/events/last/0",
                f"/team/{tid}/events/last/0/ratings",
            ]
            for p in candidates:
                res=get(p)
                if res and res[0] is not None:
                    prev=res[0].get("events",[])
                    row[f"{side}_history_endpoint"]=p
                    row[f"{side}_history_count"]=len(prev)
                    if prev:
                        pid=prev[0].get("id")
                        st=get(f"/event/{pid}/statistics")
                        row[f"{side}_sample_event"]=pid
                        row[f"{side}_stats_status"]=st[1] if st else None
                        if st and st[0]:
                            keys=stat_keys(st[0])
                            row[f"{side}_stat_keys"]=keys[:80]
                            wanted=[x for x in keys if x[0] in ("totalShotsOnGoal","shotsOnGoal","cornerKicks","shotsOffGoal","blockedShots") or any(w in (x[1] or "").lower() for w in ("shot","corner"))]
                            row[f"{side}_wanted_stats"]=wanted
                    break
                else:
                    row[f"{side}_history_last_status"]=res[1] if res else None
        report["tested"].append(row)
        time.sleep(.15)
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
