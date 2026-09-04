#!/usr/bin/env python3
"""
Calibra le soglie numeriche dell'indice dai dati storici.
Input CSV minimo:
shots_combined_p10, precision_pct, conversion_pct
Opzionale: ht_goal per verifica.
"""
import argparse, json
import numpy as np, pandas as pd
from pathlib import Path

ap=argparse.ArgumentParser()
ap.add_argument("csv")
ap.add_argument("--config",default="config.json")
args=ap.parse_args()

df=pd.read_csv(args.csv)
req=["shots_combined_p10","precision_pct","conversion_pct"]
missing=[c for c in req if c not in df.columns]
if missing: raise SystemExit(f"Colonne mancanti: {missing}")

df=df.dropna(subset=req).copy()
conv_quality=-(df["conversion_pct"]-30.0).abs()
means={
 "shots_mean":float(df["shots_combined_p10"].mean()),
 "shots_sd":float(df["shots_combined_p10"].std(ddof=0)),
 "precision_mean":float(df["precision_pct"].mean()),
 "precision_sd":float(df["precision_pct"].std(ddof=0)),
 "conversion_target":30.0,
 "conv_quality_mean":float(conv_quality.mean()),
 "conv_quality_sd":float(conv_quality.std(ddof=0)),
}
z=lambda s,m,sd:(s-m)/sd
score=.50*z(df["shots_combined_p10"],means["shots_mean"],means["shots_sd"]) \
     +.30*z(df["precision_pct"],means["precision_mean"],means["precision_sd"]) \
     +.20*z(conv_quality,means["conv_quality_mean"],means["conv_quality_sd"])
means["score_p70"]=float(score.quantile(.70))
means["score_p80"]=float(score.quantile(.80))

cfg=json.loads(Path(args.config).read_text(encoding="utf-8"))
cfg["calibration"]=means
Path(args.config).write_text(json.dumps(cfg,indent=2,ensure_ascii=False),encoding="utf-8")
print(json.dumps(means,indent=2))
if "ht_goal" in df.columns:
    for q in [.70,.80,.90]:
        th=float(score.quantile(q))
        sel=df.loc[score>=th,"ht_goal"]
        print(f"Top {100*(1-q):.0f}%: N={len(sel)} O0.5HT={100*sel.mean():.2f}%")
