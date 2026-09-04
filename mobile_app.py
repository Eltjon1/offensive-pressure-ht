#!/usr/bin/env python3
import os, threading, uuid, io, contextlib, json
from dataclasses import asdict
from flask import Flask, render_template, request, jsonify, Response
from scanner import init_db, analyse_match, today_matches
import sofascore_probe

app = Flask(__name__)
_jobs = {}
_lock = threading.Lock()

def set_job(jid, **kw):
    with _lock: _jobs.setdefault(jid, {}).update(kw)

def get_job(jid):
    with _lock: return dict(_jobs.get(jid, {}))

def summarize(results):
    s={'analysed':len(results),'odds_read':0,'odds_ok':0,'history_complete':0,
       'strong':0,'standard':0,'no_bet':0,'check':0,'errors':0}
    for r in results:
        v=r.get('verdict')
        if r.get('over25_odds') is not None: s['odds_read']+=1
        if r.get('over25_odds') is not None and 1.40<float(r['over25_odds'])<2.00: s['odds_ok']+=1
        if (r.get('home_games') or 0)>=10 and (r.get('away_games') or 0)>=10: s['history_complete']+=1
        if v=='STRONG': s['strong']+=1
        elif v=='STANDARD': s['standard']+=1
        elif v=='NO BET': s['no_bet']+=1
        elif v=='CHECK': s['check']+=1
        elif v=='ERROR': s['errors']+=1
    return s

def run_today_job(jid,max_matches):
    try:
        items=today_matches()[:max_matches]
        set_job(jid,status='running',total=len(items),done=0,results=[])
        out=[]
        for i,m in enumerate(items,1):
            try: d=asdict(analyse_match(m.get('url') or m.get('id'),feed_match=m,force_history=(i<=3)))
            except Exception as e:
                d={'home':m.get('home','?'),'away':m.get('away','?'),'match_url':m.get('url',''),
                   'over25_odds':None,'home_games':0,'away_games':0,'shots_combined_p10':None,
                   'precision_pct':None,'conversion_pct':None,'offensive_score':None,
                   'corners_combined_p4':None,'verdict':'ERROR','reason':str(e)}
            out.append(d); set_job(jid,done=i,results=out,summary=summarize(out))
        set_job(jid,status='finished',done=len(items),results=out,summary=summarize(out))
    except Exception as e: set_job(jid,status='error',error=str(e))

@app.get('/')
def index(): return render_template('index.html')

@app.get('/health')
def health(): return jsonify({'ok':True,'service':'offensive-pressure-ht','engine':'v4.3+sofascore-probe'})

@app.get('/api/sofascore-probe')
def sofa_probe():
    # Run the already-uploaded probe and return its JSON directly in the browser.
    day=(request.args.get('date') or '').strip()
    old=list(__import__('sys').argv)
    buf=io.StringIO()
    try:
        __import__('sys').argv=['sofascore_probe.py']+([day] if day else [])
        with contextlib.redirect_stdout(buf):
            sofascore_probe.main()
        raw=buf.getvalue()
        try: return jsonify(json.loads(raw))
        except Exception: return Response(raw,mimetype='text/plain')
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)}),500
    finally:
        __import__('sys').argv=old

@app.post('/api/scan-one')
def scan_one():
    data=request.get_json(silent=True) or {}; url=(data.get('url') or '').strip()
    if not url: return jsonify({'error':'Inserisci un URL partita Flashscore valido.'}),400
    try: return jsonify(asdict(analyse_match(url)))
    except Exception as e: return jsonify({'error':str(e)}),500

@app.post('/api/scan-today')
def scan_today():
    data=request.get_json(silent=True) or {}; max_matches=max(1,min(int(data.get('max_matches',100)),150))
    jid=uuid.uuid4().hex; set_job(jid,status='queued',total=0,done=0,results=[])
    threading.Thread(target=run_today_job,args=(jid,max_matches),daemon=True).start()
    return jsonify({'job_id':jid})

@app.get('/api/job/<jid>')
def job(jid):
    j=get_job(jid); return (jsonify(j),200) if j else (jsonify({'error':'Job non trovato'}),404)

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT','5050')),threaded=True)
