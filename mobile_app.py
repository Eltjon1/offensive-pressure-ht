
#!/usr/bin/env python3
import asyncio
import os
import socket
import threading
import uuid
from dataclasses import asdict
from pathlib import Path

from flask import Flask, render_template, request, jsonify
from scanner import init_db, analyse_match, today_links
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent
app = Flask(__name__)

_jobs = {}
_jobs_lock = threading.Lock()

def set_job(job_id, **kwargs):
    with _jobs_lock:
        _jobs.setdefault(job_id, {})
        _jobs[job_id].update(kwargs)

def get_job(job_id):
    with _jobs_lock:
        return dict(_jobs.get(job_id, {}))

async def new_context(p):
    browser = await p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-extensions",
        ],
    )
    context = await browser.new_context(
        locale="en-US",
        timezone_id="Europe/Rome",
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
            "Chrome/126.0.0.0 Mobile Safari/537.36"
        ),
    )
    return browser, context

async def scan_single(url):
    init_db()
    async with async_playwright() as p:
        browser, context = await new_context(p)
        try:
            result = await analyse_match(context, url)
            return asdict(result)
        finally:
            await browser.close()

async def scan_today(job_id, max_matches=50):
    init_db()
    async with async_playwright() as p:
        browser, context = await new_context(p)
        try:
            page = await context.new_page()
            try:
                urls = (await today_links(page))[:max_matches]
            finally:
                await page.close()

            set_job(job_id, total=len(urls), done=0, status="running")
            results = []
            for i, url in enumerate(urls, 1):
                try:
                    r = await analyse_match(context, url)
                    d = asdict(r)
                except Exception as e:
                    d = {
                        "home":"?", "away":"?", "match_url":url, "over25_odds":None,
                        "home_games":0, "away_games":0, "shots_combined_p10":None,
                        "precision_pct":None, "conversion_pct":None, "offensive_score":None,
                        "corners_combined_p4":None, "verdict":"ERROR", "reason":str(e)
                    }
                results.append(d)
                set_job(job_id, done=i, results=results)
            set_job(job_id, status="finished", done=len(urls), results=results)
        finally:
            await browser.close()

def run_today_job(job_id, max_matches):
    try:
        asyncio.run(scan_today(job_id, max_matches=max_matches))
    except Exception as e:
        set_job(job_id, status="error", error=str(e))

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "offensive-pressure-ht"})

@app.post("/api/scan-one")
def api_scan_one():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if "flashscore" not in url.lower():
        return jsonify({"error":"Inserisci un URL partita Flashscore valido."}), 400
    try:
        return jsonify(asyncio.run(scan_single(url)))
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.post("/api/scan-today")
def api_scan_today():
    data = request.get_json(silent=True) or {}
    max_matches = int(data.get("max_matches", 50))
    max_matches = max(1, min(max_matches, 100))
    job_id = uuid.uuid4().hex
    set_job(job_id, status="queued", total=0, done=0, results=[])
    threading.Thread(
        target=run_today_job,
        args=(job_id, max_matches),
        daemon=True
    ).start()
    return jsonify({"job_id": job_id})

@app.get("/api/job/<job_id>")
def api_job(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error":"Job non trovato"}), 404
    return jsonify(job)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
