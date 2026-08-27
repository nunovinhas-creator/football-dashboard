import json, os, glob, sys
from datetime import datetime, timezone

FROZEN_SINCE = "2026-06-10"
HISTORY_FILE = "docs/history.json"
CLV_FIELDS = [
    "bet_pin_home","bet_pin_draw","bet_pin_away",
    "bet_pin_o25","bet_pin_btts",
    "close_pin_home","close_pin_draw","close_pin_away",
    "close_pin_o25","close_pin_btts",
]
WARN = []
OK = []

def check(cond, ok_msg, fail_msg):
    if cond: OK.append("  OK  " + ok_msg)
    else:    WARN.append("  XX  " + fail_msg)
    return cond

preds_files = sorted(glob.glob("docs/preds_*.json"))
check(len(preds_files) > 0,
      f"{len(preds_files)} ficheiros preds encontrados",
      "Nenhum preds_*.json em docs/")

snap_totals, multi_snap, zero_snap = [], 0, 0
for fp in preds_files:
    try:
        data = json.load(open(fp, encoding="utf-8"))
    except Exception as e:
        WARN.append(f"  XX  {fp}: erro ({e})"); continue
    preds = data if isinstance(data, list) else data.get("predictions", [])
    for p in preds:
        snaps = p.get("_odds_snapshots", [])
        snap_totals.append(len(snaps))
        if len(snaps) >= 2: multi_snap += 1
        elif len(snaps) == 0: zero_snap += 1

if snap_totals:
    avg = round(sum(snap_totals)/len(snap_totals), 2)
    check(avg >= 1.0,
          f"{len(snap_totals)} eventos, {avg} snapshots/evento, {multi_snap} com >=2",
          f"Media {avg} snapshots/evento — esperado >=1.0")
    check(multi_snap > 0,
          f"{multi_snap} eventos com >=2 snapshots (crons 18h45/20h45 OK)",
          "Nenhum evento com >=2 snapshots — crons ainda nao correram")
else:
    WARN.append("  XX  Sem eventos nos preds para analisar")

if os.path.exists(HISTORY_FILE):
    hist = json.load(open(HISTORY_FILE, encoding="utf-8"))
    records = hist.get("records", [])
    oos = [r for r in records if (r.get("date") or "") >= FROZEN_SINCE]
    check(len(oos) > 0,
          f"{len(oos)} registos OOS (>= {FROZEN_SINCE}), {len(records)} total",
          f"Sem registos OOS ainda — SCORE nao correu desde {FROZEN_SINCE}")
    if oos:
        with_clv = [r for r in oos if any(r.get(f) is not None for f in CLV_FIELDS)]
        check(len(with_clv) > 0,
              f"{len(with_clv)}/{len(oos)} registos OOS com campos CLV",
              "Nenhum registo OOS tem campos bet_pin_*/close_pin_*")
else:
    WARN.append("  XX  history.json nao existe")

if os.path.exists("docs/freeze_manifest.json"):
    fm = json.load(open("docs/freeze_manifest.json", encoding="utf-8"))
    check(fm.get("frozen_since") == FROZEN_SINCE,
          f"freeze_manifest frozen_since={fm.get('frozen_since')} OK",
          f"freeze_manifest frozen_since errado: {fm.get('frozen_since')}")
else:
    WARN.append("  XX  freeze_manifest.json nao existe")

ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
print("="*55)
print(f"  DIAGNOSTICO C2 — {ts}")
print(f"  {len(OK)}/{len(OK)+len(WARN)} checks OK  |  {len(WARN)} avisos")
print("="*55)
for line in OK: print(line)
for line in WARN: print(line)
print("="*55)
if len(WARN) == 0:
    print("  RESULTADO: SNAPSHOTS OK")
elif multi_snap == 0 and len(WARN) <= 2:
    print("  RESULTADO: SISTEMA OK — aguardar crons 18h45/20h45 UTC")
else:
    print("  RESULTADO: VERIFICAR AVISOS")
    sys.exit(1)
