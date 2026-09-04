#!/usr/bin/env python3
import json, sys, secrets
from datetime import date
import requests

HOSTS = [
    "https://www.sofascore.com/api/v1",
    "https://api.sofascore.com/api/v1",
    "https://api.sofascore.app/api/v1",
]
UA = "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"

def attempt(base, day, mode):
    url=f"{base}/sport/football/scheduled-events/{day}"
    h={"User-Agent":UA,"Accept":"application/json","Accept-Language":"it-IT,it;q=0.9,en;q=0.8"}
    if mode=="xhr":
        h["X-Requested-With"]="XMLHttpRequest"
    elif mode=="token":
        h["X-Requested-With"]=secrets.token_hex(16)
    elif mode=="browser":
        h.update({
            "X-Requested-With":secrets.token_hex(16),
            "Referer":"https://www.sofascore.com/",
            "Origin":"https://www.sofascore.com",
            "Sec-Fetch-Site":"same-site",
            "Sec-Fetch-Mode":"cors",
            "Sec-Fetch-Dest":"empty",
        })
    try:
        r=requests.get(url,headers=h,timeout=12)
        preview=r.text[:260].replace("\n"," ")
        events=None
        if r.ok:
            try: events=len(r.json().get("events",[]))
            except Exception: pass
        return {"host":base,"mode":mode,"status":r.status_code,
                "content_type":r.headers.get("content-type"),"events":events,
                "preview":preview}
    except Exception as e:
        return {"host":base,"mode":mode,"status":None,"error":str(e)}

def main():
    day=sys.argv[1] if len(sys.argv)>1 else date.today().isoformat()
    results=[]
    for base in HOSTS:
        for mode in ("plain","xhr","token","browser"):
            results.append(attempt(base,day,mode))
    winners=[x for x in results if x.get("status")==200 and (x.get("events") or 0)>0]
    print(json.dumps({
        "ok":bool(winners),"date":day,
        "verdict":"PASS" if winners else "BLOCKED",
        "winners":winners,"tests":results
    },ensure_ascii=False))
if __name__=="__main__":
    main()
