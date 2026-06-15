"""
Diagnóstico C2 — Acumulação de snapshots de odds e campos CLV
Corre: python diagnose_snapshots.py > docs/diag_c2.txt
"""
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
OK   = []

def check(cond, ok_msg, fail_msg):
    if cond: OK.append("  ✅  " + ok_msg)
    else:    WARN.append("  ❌  " + fail_msg)
    return cond

# ── 1. preds_*.json ──────────────────────────────────────────────────────
preds_files = sorted(glob.glob("docs/preds_*.json"))
check(len(preds_files) > 0,
      f"{len(preds_files)} ficheiros preds encontrados",
      "Nenhum ficheiro preds_*.json em docs/")

snap_totals = []
multi_snap  = 0
zero_snap   = 0

for fp in preds_files:
    try:
        data = json.load(open(fp, encoding="utf-8"))
    except Exception as e:
        WARN.append(f"  ❌  {fp}: erro de leitura ({e})")
        continue
    preds = data if isinstance(data, list) else data.get("predictions", [])
    for p in preds:
        snaps = p.get("_odds_snapshots", [])
        snap_totals.append(len(snaps))
        if len(snaps) >= 2:
            multi_snap += 1
        elif len(snaps) == 0:
            zero_snap += 1

if snap_totals:
    avg = round(sum(snap_totals)/len(snap_totals), 2)
    check(avg >= 1.0,
          f"preds: {len(snap_totals)} eventos · {avg} snapshots/evento (média) · {multi_snap} com ≥2",
          f"preds: média {avg} snapshots/evento — esperado >=1.0")
    check(multi_snap > 0,
          f"{multi_snap} eventos com ≥2 snapshots (crons 18h45/20h45 a funcionar)",
          "Nenhum evento com ≥2 snapshots — crons 18h45/20h45 ainda não correram ou não apanharam odds")
    check(zero_snap == 0,
          "Sem eventos com 0 snapshots",
          f"{zero_snap} eventos com 0 snapshots (odds não guardadas nessas runs)")
else:
    WARN.append("  ❌  Sem eventos em preds_*.json para analisar")

# ── 2. history.json ───────────────────────────────────────────────────────
if not os.path.exists(HISTORY_FILE):
    WARN.append(f"  ❌  {HISTORY_FILE} não existe")
else:
    try:
        hist = json.load(open(HISTORY_FILE, encoding="utf-8"))
        records = hist.get("records", [])
    except Exception as e:
        records = []
        WARN.append(f"  ❌  history.json: erro de leitura ({e})")

    oos = [r for r in records if (r.get("date") or "") >= FROZEN_SINCE]
    check(len(oos) > 0,
          f"history.json: {len(oos)} registos OOS (>= {FROZEN_SINCE}), {len(records)} total",
          f"history.json: sem registos OOS ainda — SCORE ainda não correu desde {FROZEN_SINCE}")

    if oos:
        with_clv    = [r for r in oos if any(r.get(f) is not None for f in CLV_FIELDS)]
        all10       = [r for r in oos if all(r.get(f) is not None for f in CLV_FIELDS)]
        partial     = len(with_clv) - len(all10)
        pct = round(len(with_clv)/len(oos)*100, 1) if oos else 0

        check(len(with_clv) > 0,
              f"CLV: {len(with_clv)}/{len(oos)} registos OOS com >=1 campo CLV ({pct}%)",
              "CLV: nenhum registo OOS tem campos bet_pin_*/close_pin_* — make_record() não está a preencher")
        check(len(all10) > 0,
              f"CLV completo: {len(all10)} registos com todos os 10 campos CLV preenchidos",
              f"CLV incompleto: {partial} registos com apenas alguns campos (pode ser normal se só há 1 lado de odds)")

        # Verificar coerência: bet <= close (ou iguais se 1 snapshot)
        incoherent = 0
        for r in oos:
            for suff in ("home","draw","away","o25","btts"):
                b = r.get(f"bet_pin_{suff}")
                c = r.get(f"close_pin_{suff}")
                if b and c and abs(float(b)-float(c)) > float(b)*0.25:
                    incoherent += 1
        check(incoherent == 0,
              "Odds bet/close coerentes (sem divergência >25%)",
              f"{incoherent} campos com divergência bet/close >25% — verificar lógica _pick_bet_close_odds()")

# ── 3. freeze_manifest.json ───────────────────────────────────────────────
fm_path = "docs/freeze_manifest.json"
if os.path.exists(fm_path):
    try:
        fm = json.load(open(fm_path, encoding="utf-8"))
        check(fm.get("frozen_since") == FROZEN_SINCE,
              f"freeze_manifest: frozen_since={fm.get('frozen_since')} ✓",
              f"freeze_manifest: frozen_since={fm.get('frozen_since')} (esperado {FROZEN_SINCE})")
    except Exception:
        WARN.append("  ❌  freeze_manifest.json: erro de leitura")
else:
    WARN.append("  ❌  docs/freeze_manifest.json não existe — corre mdb_analytics.py uma vez")

# ── Relatório ──────────────────────────────────────────────────────────────
ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
total = len(OK) + len(WARN)
n_ok  = len(OK)
n_fail= len(WARN)

print("═"*60)
print(f"  DIAGNÓSTICO C2 — {ts}")
print(f"  {n_ok}/{total} checks OK  |  {n_fail} avisos")
print("═"*60)
for line in OK:
    print(line)
for line in WARN:
    print(line)
print("═"*60)

if n_fail == 0:
    print("  RESULTADO: SNAPSHOTS A ACUMULAR CORRECTAMENTE ✅")
    print("  Próximo marco: C3 (30 Jun) — primeiro OOS real com CLV.")
elif multi_snap == 0 and n_fail <= 2:
    print("  RESULTADO: SISTEMA OK — aguardar runs 18h45/20h45 UTC ⏳")
    print("  Se hoje já passaram essas horas e multi_snap=0, verifica")
    print("  os logs do workflow para as runs de cron extra.")
else:
    print("  RESULTADO: VERIFICAR AVISOS ACIMA ⚠️")
    sys.exit(1)
