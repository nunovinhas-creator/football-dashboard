"""
Matemática Da Bola — Backtest v3 (triplas)
Modo SAVE  (07:00/14:00/21:00 UTC): guarda predições + odds Pinnacle; constrói tripla do dia
Modo SCORE (00:00 UTC + sempre): cruza predições com resultados; pontua triplas; gera HTML
"""

import os
import json
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

BSD_KEY      = os.environ["BSD_API_KEY"]
BASE         = "https://sports.bzzoiro.com/api/v2"
HEADERS      = {"Authorization": f"Token {BSD_KEY}"}
HISTORY_FILE = "docs/history.json"
TREBLES_FILE = "docs/trebles.json"

# ── Helpers ───────────────────────────────────────────────────────────────────

def get(path, params=None):
    r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def yesterday_str():
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def preds_file(date_str):
    return f"docs/preds_{date_str}.json"

# ── Persistência ──────────────────────────────────────────────────────────────

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"records": [], "dates_processed": [], "dates_partial": {}}

def save_history(h):
    os.makedirs("docs", exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, separators=(",", ":"))

def load_trebles():
    if os.path.exists(TREBLES_FILE):
        try:
            with open(TREBLES_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"pending": [], "history": []}

def save_trebles(t):
    os.makedirs("docs", exist_ok=True)
    with open(TREBLES_FILE, "w", encoding="utf-8") as f:
        json.dump(t, f, ensure_ascii=False, separators=(",", ":"))

# ── API ───────────────────────────────────────────────────────────────────────

def fetch_pinnacle_odds(event_id):
    try:
        data = get(f"/events/{event_id}/odds/comparison/")
        for b in (data.get("bookmakers") or []):
            name = (b.get("bookmaker_name") or "").lower()
            slug = b.get("bookmaker_slug", "")
            if "pinnacle" in name or slug == "pinnacle":
                return b
    except Exception:
        pass
    return {}

def fetch_todays_predictions():
    today = today_str()
    all_preds = []
    offset = 0
    while True:
        try:
            data = get("/predictions/", {"limit": 50, "offset": offset})
            results = data.get("results", [])
            if not results:
                break
            for r in results:
                ed = r.get("event", {}).get("event_date", "")[:10]
                if ed == today:
                    eid = r.get("event", {}).get("id")
                    if eid:
                        r["_pinnacle_odds"] = fetch_pinnacle_odds(eid)
                    all_preds.append(r)
            if not data.get("next"):
                break
            offset += 50
        except Exception as e:
            print(f"  [WARN] offset={offset}: {e}")
            break
    return all_preds

def fetch_event_result(event_id):
    try:
        ev = get(f"/events/{event_id}/")
        if not ev:
            return None
        status = ev.get("status", "")
        period = ev.get("period", "")
        if status != "finished" and period != "FT":
            return None
        hs  = ev.get("home_score")
        as_ = ev.get("away_score")
        if hs is None or as_ is None:
            return None
        return {"home_score": int(hs), "away_score": int(as_)}
    except Exception:
        return None

# ── Thresholds actualizados por mercado ───────────────────────────────────────
#
# pick_1x2:  só confiança MÉDIA (ALTA estava a 33%, BAIXA < 50%)
# pick_o25:  xG total >= 2.9 (o po do modelo estava descalibrado a 47% em qualquer threshold)
# pick_btts: pb >= 61% E confiança ALTA ou MÉDIA (BAIXA estava a 45%)
# pick_xg:   xG total >= 2.8 (antes era "sempre true" — 100% das linhas)

def make_record(pred, result):
    event   = pred.get("event", {})
    markets = pred.get("markets", {})
    mr      = markets.get("match_result", {})
    ou      = markets.get("over_under", {})
    bt      = markets.get("btts", {})
    xg      = markets.get("expected_goals", {})
    model   = pred.get("model", {})
    pin     = pred.get("_pinnacle_odds", {})

    hs    = int(result["home_score"])
    as_   = int(result["away_score"])
    goals = hs + as_

    ph = float(mr.get("prob_home") or 0)
    pd = float(mr.get("prob_draw") or 0)
    pa = float(mr.get("prob_away") or 0)
    po = float(ou.get("prob_over_25") or 0)
    pb = float(bt.get("prob_yes") or 0)
    xgh = float(xg.get("home") or 0)
    xga = float(xg.get("away") or 0)
    xgt = round(xgh + xga, 2)
    conf_val = float(model.get("confidence") or 0)

    if hs > as_:    real = "H"
    elif hs == as_: real = "D"
    else:           real = "A"

    best = max(ph, pd, pa)
    if best == ph:   pred_r = "H"
    elif best == pd: pred_r = "D"
    else:            pred_r = "A"

    if conf_val >= 0.65:   conf = "ALTA"
    elif conf_val >= 0.45: conf = "MÉDIA"
    else:                  conf = "BAIXA"

    event_date = event.get("event_date", "")
    dt = parse_dt(event_date)
    date_str = dt.strftime("%Y-%m-%d") if dt else event_date[:10]

    return {
        "date":     date_str,
        "event_id": event.get("id"),
        "league":   event.get("league_name", "?"),
        "home":     event.get("home_team", "?"),
        "away":     event.get("away_team", "?"),
        "hs": hs, "as": as_, "goals": goals,
        "ph": round(ph,1), "pd": round(pd,1), "pa": round(pa,1),
        "po": round(po,1), "pb": round(pb,1), "xg": xgt,
        "conf": conf,
        "pred": pred_r, "real": real,
        "pick_1x2":  best >= 61 and conf == "MÉDIA",
        "pick_o25":  xgt >= 2.9 and conf in ("ALTA", "MÉDIA"),
        "pick_btts": pb >= 61 and conf in ("ALTA", "MÉDIA"),
        "pick_xg":   xgt >= 2.8,
        "hit_1x2":   pred_r == real,
        "hit_o25":   goals > 2,
        "hit_btts":  hs > 0 and as_ > 0,
        # Pinnacle odds guardadas no momento da previsão (para ROI futuro)
        "pin_home":  pin.get("home_odds"),
        "pin_draw":  pin.get("draw_odds"),
        "pin_away":  pin.get("away_odds"),
        "pin_btts":  pin.get("btts_yes"),
        "pin_o25":   pin.get("over_2_5"),
    }

def migrate_picks(records):
    """Recalcula pick_* para todos os registos com os novos thresholds."""
    for r in records:
        conf = r.get("conf", "BAIXA")
        best = max(r.get("ph", 0), r.get("pd", 0), r.get("pa", 0))
        pb   = r.get("pb", 0)
        xgt  = r.get("xg", 0)
        r["pick_1x2"]  = best >= 61 and conf == "MÉDIA"
        r["pick_o25"]  = xgt >= 2.9 and conf in ("ALTA", "MÉDIA")
        r["pick_btts"] = pb >= 61 and conf in ("ALTA", "MÉDIA")
        r["pick_xg"]   = xgt >= 2.8
    return records

# ── Builder de Triplas ────────────────────────────────────────────────────────

EXCLUDED_LEAGUES = {
    # xG sistematicamente sobreavaliado (-0.54 golos médios); 0% hit rate BTTS e O25 em amostra
    "Saudi Pro League",
}

def build_daily_treble(preds):
    """
    Selecciona a melhor tripla do dia.
    Prioridade 1: BTTS com confiança ALTA ou MÉDIA (92% de acerto histórico)
    Prioridade 2: 1X2 com confiança MÉDIA (100% de acerto histórico, amostra pequena)
    Regra: máximo 1 pick por liga para evitar correlação
    Ligas excluídas: xG sobreavaliado sistematicamente (ver EXCLUDED_LEAGUES)
    """
    today = today_str()
    candidates = []

    for p in preds:
        event  = p.get("event", {})
        mkts   = p.get("markets", {})
        bt     = mkts.get("btts", {})
        mr     = mkts.get("match_result", {})
        model  = p.get("model", {})
        pin    = p.get("_pinnacle_odds", {})

        pb       = float(bt.get("prob_yes") or 0)
        ph       = float(mr.get("prob_home") or 0)
        pd_v     = float(mr.get("prob_draw") or 0)
        pa       = float(mr.get("prob_away") or 0)
        conf_val = float(model.get("confidence") or 0)

        if conf_val >= 0.65:   conf = "ALTA"
        elif conf_val >= 0.45: conf = "MÉDIA"
        else:                  conf = "BAIXA"

        league = event.get("league_name", "?")
        eid    = event.get("id")

        if league in EXCLUDED_LEAGUES:
            continue

        # Prioridade 1: BTTS ALTA ou MÉDIA
        if pb >= 61 and conf in ("ALTA", "MÉDIA"):
            pin_odds = pin.get("btts_yes")
            candidates.append({
                "priority": 1,
                "event_id": eid,
                "league":   league,
                "home":     event.get("home_team", "?"),
                "away":     event.get("away_team", "?"),
                "market":   "BTTS",
                "prob":     round(pb / 100, 3),
                "conf":     conf,
                "odds":     float(pin_odds) if pin_odds else None,
            })
            continue

        # Prioridade 2: 1X2 MÉDIA
        best = max(ph, pd_v, pa)
        if best >= 61 and conf == "MÉDIA":
            if best == ph:     side, ok = "H", "home_odds"
            elif best == pd_v: side, ok = "D", "draw_odds"
            else:              side, ok = "A", "away_odds"
            pin_odds = pin.get(ok)
            candidates.append({
                "priority": 2,
                "event_id": eid,
                "league":   league,
                "home":     event.get("home_team", "?"),
                "away":     event.get("away_team", "?"),
                "market":   f"1X2-{side}",
                "prob":     round(best / 100, 3),
                "conf":     conf,
                "odds":     float(pin_odds) if pin_odds else None,
            })

    # 1 pick por liga, melhor por prioridade depois prob
    seen = {}
    for c in sorted(candidates, key=lambda x: (x["priority"], -x["prob"])):
        if c["league"] not in seen:
            seen[c["league"]] = c
    unique = list(seen.values())

    if len(unique) < 3:
        return None

    picks = sorted(unique, key=lambda x: (x["priority"], -x["prob"]))[:3]
    for p in picks:
        p.pop("priority", None)

    odds_vals = [p["odds"] for p in picks if p.get("odds")]
    combined = None
    if len(odds_vals) == 3:
        combined = round(odds_vals[0] * odds_vals[1] * odds_vals[2], 2)

    return {"date": today, "picks": picks, "combined_odds": combined, "status": "pending"}

def score_treble(treble, records_for_date):
    """Pontua uma tripla pendente usando os registos reais do dia."""
    by_match = {}
    for r in records_for_date:
        key = (r.get("league", ""), r.get("home", ""), r.get("away", ""))
        by_match[key] = r

    results = []
    for pick in treble["picks"]:
        key = (pick["league"], pick["home"], pick["away"])
        rec = by_match.get(key)
        if not rec:
            return None  # resultado ainda não disponível
        market = pick["market"]
        if market == "BTTS":
            hit = rec.get("hs", 0) > 0 and rec.get("as", 0) > 0
        elif market == "1X2-H":
            hit = rec.get("real") == "H"
        elif market == "1X2-D":
            hit = rec.get("real") == "D"
        elif market == "1X2-A":
            hit = rec.get("real") == "A"
        else:
            hit = False
        results.append(hit)

    won   = all(results)
    odds  = treble.get("combined_odds")
    if won and odds:
        profit = round(odds - 1, 2)
    elif won:
        profit = None   # ganhou mas odds não foram guardadas na época
    else:
        profit = -1.0

    return {
        **treble,
        "status":        "scored",
        "pick_results":  results,
        "hit":           won,
        "profit_1u":     profit,
    }

# ── Stats e HTML ──────────────────────────────────────────────────────────────

def calc_stats(records, pick_key, hit_key, label):
    subset = [r for r in records if r.get(pick_key)]
    if not subset:
        return {"label": label, "picks": 0, "hits": 0, "rate": 0.0, "by_conf": {}, "trend": []}
    hits = sum(1 for r in subset if r.get(hit_key))
    rate = round(hits / len(subset) * 100, 1)
    by_conf = {}
    for c in ["ALTA", "MÉDIA", "BAIXA"]:
        sub = [r for r in subset if r["conf"] == c]
        if sub:
            h = sum(1 for r in sub if r.get(hit_key))
            by_conf[c] = {"picks": len(sub), "hits": h, "rate": round(h/len(sub)*100,1)}
    weekly = defaultdict(lambda: {"p":0,"h":0})
    for r in subset:
        try:
            dt = parse_dt(r["date"] + "T00:00:00Z")
            if dt:
                wk = dt.strftime("%Y-W%V")
                weekly[wk]["p"] += 1
                if r.get(hit_key): weekly[wk]["h"] += 1
        except Exception:
            pass
    trend = [{"w":wk,"rate":round(v["h"]/v["p"]*100,1),"p":v["p"]}
             for wk,v in sorted(weekly.items())[-8:] if v["p"]>=3]
    return {"label":label,"picks":len(subset),"hits":hits,"rate":rate,"by_conf":by_conf,"trend":trend}

def calc_xg(records):
    s = [r for r in records if r.get("pick_xg") and r.get("xg",0)>0]
    if not s: return {"picks":0,"avg_xg":0,"avg_goals":0,"over_rate":0}
    return {
        "picks":     len(s),
        "avg_xg":    round(sum(r["xg"] for r in s)/len(s),2),
        "avg_goals": round(sum(r["goals"] for r in s)/len(s),2),
        "over_rate": round(sum(1 for r in s if r["goals"]>r["xg"])/len(s)*100,1),
    }

def calc_xg_analysis(records):
    valid = [r for r in records if r.get("xg", 0) > 0 and r.get("goals") is not None]
    if not valid:
        return None

    by_league = defaultdict(lambda: {"xg": [], "g": []})
    by_date   = defaultdict(lambda: {"xg": [], "g": []})
    for r in valid:
        by_league[r["league"]]["xg"].append(r["xg"])
        by_league[r["league"]]["g"].append(r["goals"])
        by_date[r["date"]]["xg"].append(r["xg"])
        by_date[r["date"]]["g"].append(r["goals"])

    league_stats = []
    for lg, d in by_league.items():
        if len(d["xg"]) < 3:
            continue
        ax = round(sum(d["xg"]) / len(d["xg"]), 2)
        ag = round(sum(d["g"]) / len(d["g"]), 2)
        league_stats.append({"league": lg, "n": len(d["xg"]), "avg_xg": ax, "avg_goals": ag, "diff": round(ag - ax, 2)})
    league_stats.sort(key=lambda x: x["diff"])

    date_trend = []
    for dt in sorted(by_date):
        d = by_date[dt]
        date_trend.append({
            "date": dt[-5:],
            "avg_xg":    round(sum(d["xg"]) / len(d["xg"]), 2),
            "avg_goals": round(sum(d["g"]) / len(d["g"]), 2),
            "n": len(d["xg"]),
        })

    errors = [r["goals"] - r["xg"] for r in valid]
    buckets = [
        ("< −2",  sum(1 for e in errors if e < -2)),
        ("−2 a −1", sum(1 for e in errors if -2 <= e < -1)),
        ("−1 a 0",  sum(1 for e in errors if -1 <= e < 0)),
        ("0 a 1",   sum(1 for e in errors if 0 <= e < 1)),
        ("1 a 2",   sum(1 for e in errors if 1 <= e < 2)),
        ("> 2",    sum(1 for e in errors if e >= 2)),
    ]

    n = len(valid)
    over_n = sum(1 for e in errors if e > 0)
    under_n = sum(1 for e in errors if e < 0)
    avg_err = round(sum(errors) / n, 2)

    return {
        "scatter": [{"xg": r["xg"], "goals": r["goals"], "league": r["league"], "date": r["date"]} for r in valid],
        "league_stats": league_stats,
        "date_trend":   date_trend,
        "buckets":      buckets,
        "summary": {
            "n": n,
            "avg_xg":    round(sum(r["xg"]    for r in valid) / n, 2),
            "avg_goals": round(sum(r["goals"]  for r in valid) / n, 2),
            "avg_err":   avg_err,
            "over_pct":  round(over_n  / n * 100),
            "under_pct": round(under_n / n * 100),
        },
    }

def _scatter_svg(scatter):
    W, H = 400, 300
    ML, MR, MT, MB = 42, 16, 16, 38
    pw, ph = W - ML - MR, H - MT - MB
    MX = 6.5

    def sx(v): return ML + min(v, MX) / MX * pw
    def sy(v): return MT + ph - min(v, MX) / MX * ph

    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="display:block;max-width:{W}px">']
    # grid + labels
    for i in range(0, 8):
        x, y = sx(i), sy(i)
        out.append(f'<line x1="{x:.0f}" y1="{MT}" x2="{x:.0f}" y2="{MT+ph}" stroke="#1e2a3a" stroke-width="1"/>')
        out.append(f'<line x1="{ML}" y1="{y:.0f}" x2="{ML+pw}" y2="{y:.0f}" stroke="#1e2a3a" stroke-width="1"/>')
        if i > 0:
            out.append(f'<text x="{x:.0f}" y="{MT+ph+14}" fill="#4a5568" font-size="10" text-anchor="middle">{i}</text>')
            out.append(f'<text x="{ML-6}" y="{y+3:.0f}" fill="#4a5568" font-size="10" text-anchor="end">{i}</text>')
    # Over 2.5 dashed threshold
    x25, y25 = sx(2.5), sy(2.5)
    out.append(f'<line x1="{x25:.0f}" y1="{MT}" x2="{x25:.0f}" y2="{MT+ph}" stroke="#4a5568" stroke-width="1" stroke-dasharray="4,3"/>')
    out.append(f'<line x1="{ML}" y1="{y25:.0f}" x2="{ML+pw}" y2="{y25:.0f}" stroke="#4a5568" stroke-width="1" stroke-dasharray="4,3"/>')
    out.append(f'<text x="{x25+3:.0f}" y="{MT+10}" fill="#4a5568" font-size="9">2.5</text>')
    # Perfect calibration diagonal
    out.append(f'<line x1="{sx(0):.0f}" y1="{sy(0):.0f}" x2="{sx(MX):.0f}" y2="{sy(MX):.0f}" stroke="#f87171" stroke-width="1.5" stroke-dasharray="6,4" opacity="0.6"/>')
    # Points
    for d in scatter:
        xg, g = d["xg"], d["goals"]
        diff = g - xg
        col = "#4ade80" if diff > 0.3 else ("#f87171" if diff < -0.3 else "#60a5fa")
        lg  = d["league"].replace('"', "'").replace('<', '')
        out.append(f'<circle cx="{sx(xg):.1f}" cy="{sy(g):.1f}" r="4.5" fill="{col}" opacity="0.75" stroke="#0d1117" stroke-width="1"><title>{lg}\nxG={xg:.1f}  Golos={g}</title></circle>')
    # Axis labels
    out.append(f'<text x="{ML+pw/2:.0f}" y="{H-2}" fill="#94a3b8" font-size="11" text-anchor="middle">xG Previsto</text>')
    out.append(f'<text x="11" y="{MT+ph/2:.0f}" fill="#94a3b8" font-size="11" text-anchor="middle" transform="rotate(-90,11,{MT+ph/2:.0f})">Golos Reais</text>')
    # Legend
    out.append(f'<circle cx="{ML+4}" cy="{MT+6}" r="4" fill="#4ade80" opacity="0.8"/><text x="{ML+12}" y="{MT+10}" fill="#4ade80" font-size="9">Subestimado</text>')
    out.append(f'<circle cx="{ML+80}" cy="{MT+6}" r="4" fill="#f87171" opacity="0.8"/><text x="{ML+88}" y="{MT+10}" fill="#f87171" font-size="9">Sobreavaliado</text>')
    out.append(f'<circle cx="{ML+168}" cy="{MT+6}" r="4" fill="#60a5fa" opacity="0.8"/><text x="{ML+176}" y="{MT+10}" fill="#60a5fa" font-size="9">Calibrado</text>')
    out.append('</svg>')
    return "".join(out)

def _trend_svg(date_trend):
    if len(date_trend) < 2:
        return ""
    W, H = 420, 180
    ML, MR, MT, MB = 42, 16, 24, 34
    pw, ph = W - ML - MR, H - MT - MB
    n = len(date_trend)
    all_vals = [d["avg_xg"] for d in date_trend] + [d["avg_goals"] for d in date_trend]
    MX = max(all_vals) * 1.15 or 4.0

    def sx(i): return ML + i / (n - 1) * pw if n > 1 else ML + pw / 2
    def sy(v): return MT + ph - v / MX * ph

    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" style="display:block;max-width:{W}px">']
    # horizontal grid
    for v in [1, 2, 3, 4]:
        if v <= MX:
            y = sy(v)
            out.append(f'<line x1="{ML}" y1="{y:.0f}" x2="{ML+pw}" y2="{y:.0f}" stroke="#1e2a3a" stroke-width="1"/>')
            out.append(f'<text x="{ML-6}" y="{y+3:.0f}" fill="#4a5568" font-size="10" text-anchor="end">{v}</text>')
    # xG line
    xg_pts = " ".join(f"{sx(i):.1f},{sy(d['avg_xg']):.1f}" for i, d in enumerate(date_trend))
    g_pts  = " ".join(f"{sx(i):.1f},{sy(d['avg_goals']):.1f}" for i, d in enumerate(date_trend))
    out.append(f'<polyline points="{xg_pts}" fill="none" stroke="#60a5fa" stroke-width="2.5" stroke-linejoin="round"/>')
    out.append(f'<polyline points="{g_pts}" fill="none" stroke="#4ade80" stroke-width="2.5" stroke-linejoin="round"/>')
    for i, d in enumerate(date_trend):
        out.append(f'<circle cx="{sx(i):.1f}" cy="{sy(d["avg_xg"]):.1f}" r="4" fill="#60a5fa"><title>{d["date"]}: xG médio={d["avg_xg"]}</title></circle>')
        out.append(f'<circle cx="{sx(i):.1f}" cy="{sy(d["avg_goals"]):.1f}" r="4" fill="#4ade80"><title>{d["date"]}: Golos médios={d["avg_goals"]}</title></circle>')
        out.append(f'<text x="{sx(i):.1f}" y="{MT+ph+14}" fill="#4a5568" font-size="9" text-anchor="middle">{d["date"]}</text>')
    # legend
    out.append(f'<line x1="{ML}" y1="{MT-8}" x2="{ML+16}" y2="{MT-8}" stroke="#60a5fa" stroke-width="2.5"/>')
    out.append(f'<text x="{ML+20}" y="{MT-4}" fill="#60a5fa" font-size="10">xG Previsto</text>')
    out.append(f'<line x1="{ML+90}" y1="{MT-8}" x2="{ML+106}" y2="{MT-8}" stroke="#4ade80" stroke-width="2.5"/>')
    out.append(f'<text x="{ML+110}" y="{MT-4}" fill="#4ade80" font-size="10">Golos Reais</text>')
    out.append('</svg>')
    return "".join(out)

def xg_analysis_html(records):
    data = calc_xg_analysis(records)
    if not data:
        return "", ""

    s   = data["summary"]
    err_col = "#4ade80" if s["avg_err"] >= -0.1 else "#f87171"

    # Cartões de sumário
    summary_html = (
        f'<div class="xga-summary">'
        f'<div class="xga-card"><div class="xga-n">{s["n"]}</div><div class="xga-l">Jogos</div></div>'
        f'<div class="xga-card"><div class="xga-n" style="color:#60a5fa">{s["avg_xg"]}</div><div class="xga-l">xG Médio Previsto</div></div>'
        f'<div class="xga-card"><div class="xga-n" style="color:#4ade80">{s["avg_goals"]}</div><div class="xga-l">Golos Médios Reais</div></div>'
        f'<div class="xga-card"><div class="xga-n" style="color:{err_col}">{"+" if s["avg_err"]>=0 else ""}{s["avg_err"]}</div><div class="xga-l">Erro Médio (g−xG)</div></div>'
        f'<div class="xga-card"><div class="xga-n" style="color:#f87171">{s["under_pct"]}%</div><div class="xga-l">Modelo Sobreavalia</div></div>'
        f'<div class="xga-card"><div class="xga-n" style="color:#4ade80">{s["over_pct"]}%</div><div class="xga-l">Modelo Subestima</div></div>'
        f'</div>'
    )

    # Scatter plot
    scatter_html = (
        f'<div class="xga-panel">'
        f'<div class="xga-panel-title">Calibração — xG Previsto vs Golos Reais</div>'
        f'<div class="xga-panel-sub">Linha vermelha = modelo perfeito · Linhas tracejadas = limiar Over 2.5 · Toca num ponto para ver o jogo</div>'
        f'{_scatter_svg(data["scatter"])}'
        f'</div>'
    )

    # Histograma de erro
    max_bucket = max(v for _, v in data["buckets"]) or 1
    bucket_bars = ""
    for label, cnt in data["buckets"]:
        pct = cnt / max_bucket * 100
        # negativo = modelo sobravalia (vermelho), positivo = subestima (verde)
        col = "#f87171" if label.startswith(("< −", "−")) else ("#4ade80" if label.startswith((">", "1", "0")) else "#60a5fa")
        if label in ("−1 a 0", "0 a 1"):
            col = "#fbbf24"
        bucket_bars += (
            f'<div class="xga-bucket">'
            f'<div class="xga-bucket-lbl">{label}</div>'
            f'<div class="xga-bucket-bar-bg"><div class="xga-bucket-bar-fill" style="width:{pct:.0f}%;background:{col}"></div></div>'
            f'<div class="xga-bucket-n">{cnt}</div>'
            f'</div>'
        )
    hist_html = (
        f'<div class="xga-panel">'
        f'<div class="xga-panel-title">Distribuição do Erro (Golos − xG)</div>'
        f'<div class="xga-panel-sub">Negativo = modelo sobreavalia · Positivo = modelo subestima</div>'
        f'<div class="xga-buckets">{bucket_bars}</div>'
        f'</div>'
    )

    # Tendência por data
    trend_html = (
        f'<div class="xga-panel">'
        f'<div class="xga-panel-title">Evolução Diária — xG vs Golos</div>'
        f'<div class="xga-panel-sub">Média por dia · Passa o rato sobre os pontos para ver detalhes</div>'
        f'{_trend_svg(data["date_trend"])}'
        f'</div>'
    )

    # Tabela por liga
    league_rows = ""
    for lg in data["league_stats"]:
        diff = lg["diff"]
        diff_col = "#4ade80" if diff > 0.3 else ("#f87171" if diff < -0.3 else "#fbbf24")
        diff_str = f'{"+" if diff>=0 else ""}{diff:.2f}'
        max_v    = max(lg["avg_xg"], lg["avg_goals"]) or 1
        xg_w     = int(lg["avg_xg"] / 5 * 100)
        g_w      = int(lg["avg_goals"] / 5 * 100)
        league_rows += (
            f'<tr>'
            f'<td class="tdl" style="white-space:nowrap">{lg["league"]}</td>'
            f'<td class="tdn" style="color:var(--muted)">{lg["n"]}</td>'
            f'<td style="min-width:120px;padding:6px">'
            f'  <div style="font-size:.65rem;color:#60a5fa;margin-bottom:2px">{lg["avg_xg"]:.2f}</div>'
            f'  <div style="height:4px;background:#1e2a3a;border-radius:2px"><div style="width:{xg_w}%;height:100%;background:#60a5fa;border-radius:2px"></div></div>'
            f'</td>'
            f'<td style="min-width:120px;padding:6px">'
            f'  <div style="font-size:.65rem;color:#4ade80;margin-bottom:2px">{lg["avg_goals"]:.2f}</div>'
            f'  <div style="height:4px;background:#1e2a3a;border-radius:2px"><div style="width:{g_w}%;height:100%;background:#4ade80;border-radius:2px"></div></div>'
            f'</td>'
            f'<td class="tdn" style="color:{diff_col};font-weight:700;font-size:.9rem">{diff_str}</td>'
            f'</tr>'
        )
    league_html = (
        f'<div class="xga-panel">'
        f'<div class="xga-panel-title">Calibração por Liga (mín. 3 jogos)</div>'
        f'<div class="xga-panel-sub">Negativo = modelo sobrestima golos · Positivo = modelo subestima golos</div>'
        f'<table class="ct" style="margin-top:12px">'
        f'<thead><tr><th>Liga</th><th>Jogos</th><th style="min-width:120px">xG Médio</th>'
        f'<th style="min-width:120px">Golos Médios</th><th>Diferença</th></tr></thead>'
        f'<tbody>{league_rows}</tbody></table>'
        f'</div>'
    )

    # Layout 2 colunas no top (scatter + histogram), depois full-width
    body = (
        f'<div class="stitle" style="margin-top:28px">Análise Avançada xG</div>'
        f'{summary_html}'
        f'<div class="xga-grid2">'
        f'{scatter_html}'
        f'{hist_html}'
        f'</div>'
        f'{trend_html}'
        f'{league_html}'
    )

    css = (
        '.xga-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-bottom:20px}'
        '.xga-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;text-align:center}'
        '.xga-n{font-size:1.5rem;font-weight:800;line-height:1}'
        '.xga-l{font-size:.62rem;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-top:4px}'
        '.xga-grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px;margin-bottom:14px}'
        '.xga-panel{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:14px}'
        '.xga-panel-title{font-size:.9rem;font-weight:700;margin-bottom:4px}'
        '.xga-panel-sub{font-size:.68rem;color:var(--muted);margin-bottom:14px}'
        '.xga-buckets{display:flex;flex-direction:column;gap:8px}'
        '.xga-bucket{display:flex;align-items:center;gap:10px}'
        '.xga-bucket-lbl{width:60px;font-size:.72rem;color:var(--sub);text-align:right;flex-shrink:0}'
        '.xga-bucket-bar-bg{flex:1;height:16px;background:#0f1420;border-radius:4px;overflow:hidden}'
        '.xga-bucket-bar-fill{height:100%;border-radius:4px;transition:width .4s}'
        '.xga-bucket-n{width:28px;font-size:.75rem;font-weight:700;color:var(--text);text-align:right;flex-shrink:0}'
    )

    return css, body

def top_leagues(records, pick_key, hit_key, n=8):
    by = defaultdict(lambda:{"p":0,"h":0})
    for r in records:
        if r.get(pick_key):
            by[r["league"]]["p"]+=1
            if r.get(hit_key): by[r["league"]]["h"]+=1
    rows=[{"league":lg,"picks":v["p"],"rate":round(v["h"]/v["p"]*100,1)}
          for lg,v in by.items() if v["p"]>=5]
    return sorted(rows,key=lambda x:x["rate"],reverse=True)[:n]

def rc(rate):
    if rate>=65: return "#4ade80"
    if rate>=55: return "#fbbf24"
    return "#f87171"

def treble_roi(trebles_history):
    hist = [t for t in trebles_history if t.get("status") == "scored"]
    if not hist:
        return {"total":0,"won":0,"rate":0,"staked":0,"returned":0,"roi_pct":None,"has_odds":False}
    total  = len(hist)
    won    = sum(1 for t in hist if t.get("hit"))
    # apenas inclui no ROI financeiro as triplas onde as odds foram guardadas
    with_odds = [t for t in hist if t.get("combined_odds")]
    has_odds  = bool(with_odds)
    staked    = float(len(with_odds))
    returned  = sum(t["combined_odds"] for t in with_odds if t.get("hit"))
    roi_pct   = round((returned - staked) / staked * 100, 1) if staked else None
    return {
        "total":    total,
        "won":      won,
        "rate":     round(won/total*100, 1) if total else 0,
        "staked":   staked,
        "returned": round(returned, 2),
        "roi_pct":  roi_pct,
        "has_odds": has_odds,
    }

def treble_section_html(trebles_data):
    pending = trebles_data.get("pending", [])
    history = trebles_data.get("history", [])
    roi     = treble_roi(history)

    today_treble = next((t for t in pending if t.get("status") == "pending"), None)

    mkt_label = {
        "BTTS":  "🔁 BTTS",
        "1X2-H": "🏠 Casa",
        "1X2-D": "🤝 Empate",
        "1X2-A": "✈️ Fora",
    }
    conf_color = {"ALTA": "#4ade80", "MÉDIA": "#fbbf24", "BAIXA": "#f87171"}

    # Tripla de hoje
    if today_treble:
        picks_html = ""
        for i, pk in enumerate(today_treble["picks"], 1):
            col   = conf_color.get(pk.get("conf",""), "#94a3b8")
            mkt   = mkt_label.get(pk["market"], pk["market"])
            odds  = f"{pk['odds']:.2f}" if pk.get("odds") else "–"
            picks_html += (
                f'<div class="tp">'
                f'<span class="tpn">{i}</span>'
                f'<div class="tpi">'
                f'<div class="tpl">{pk["league"]}</div>'
                f'<div class="tpm">{pk["home"]} <span style="color:var(--muted)">vs</span> {pk["away"]}</div>'
                f'</div>'
                f'<div class="tpr">'
                f'<span class="tpk">{mkt}</span>'
                f'<span style="color:{col};font-weight:700">{int(pk["prob"]*100)}%</span>'
                f'<span class="tpo">@{odds}</span>'
                f'</div></div>'
            )
        combined = f"{today_treble['combined_odds']:.2f}" if today_treble.get("combined_odds") else "–"
        today_html = (
            f'<div class="tb-today">'
            f'<div class="tb-today-hdr">'
            f'<span>🎯 Tripla de Hoje — {today_treble["date"]}</span>'
            f'<span class="tb-odds">Odds combinadas: <b>{combined}</b></span>'
            f'</div>'
            f'{picks_html}'
            f'<div class="tb-note">Aposta 1 unidade → retorno {combined} unidades se ganhar</div>'
            f'</div>'
        )
    else:
        today_html = '<div class="tb-empty">Sem tripla para hoje (picks insuficientes com alta confiança).</div>'

    # ROI summary
    if roi["roi_pct"] is not None:
        roi_col  = "#4ade80" if roi["roi_pct"] >= 0 else "#f87171"
        roi_str  = f'{"+" if roi["roi_pct"]>=0 else ""}{roi["roi_pct"]}%'
    else:
        roi_col  = "#94a3b8"
        roi_str  = "N/D"
    roi_html = (
        f'<div class="tb-roi">'
        f'<div class="tb-roi-item"><div class="tb-roi-n">{roi["total"]}</div><div class="tb-roi-l">Triplas</div></div>'
        f'<div class="tb-roi-item"><div class="tb-roi-n">{roi["won"]}</div><div class="tb-roi-l">Ganhas</div></div>'
        f'<div class="tb-roi-item"><div class="tb-roi-n">{roi["rate"]}%</div><div class="tb-roi-l">Hit Rate</div></div>'
        f'<div class="tb-roi-item"><div class="tb-roi-n" style="color:{roi_col}">{roi_str}</div>'
        f'<div class="tb-roi-l">ROI{" (sem odds)" if not roi["has_odds"] else ""}</div></div>'
        f'</div>'
    )

    # Histórico de triplas
    scored = sorted([t for t in history if t.get("status")=="scored"], key=lambda x: x["date"], reverse=True)
    if scored:
        rows = ""
        for t in scored[:10]:
            won_t   = t.get("hit", False)
            icon    = "✓" if won_t else "✗"
            icon_c  = "#4ade80" if won_t else "#f87171"
            profit  = t.get("profit_1u")
            if won_t:
                p_str = f'+{profit:.2f}u' if profit is not None else 'odds N/D'
            else:
                p_str = '-1.00u'
            p_col   = "#4ade80" if won_t else "#f87171"
            odds_d  = f"{t['combined_odds']:.2f}" if t.get("combined_odds") else "–"
            mkt_str = " · ".join(
                f'{mkt_label.get(pk["market"], pk["market"])} {pk["home"][:12]}'
                for pk in t.get("picks", [])
            )
            results = t.get("pick_results", [])
            r_icons = "".join("✓" if r else "✗" for r in results)
            rows += (
                f'<tr>'
                f'<td class="tdc">{t["date"]}</td>'
                f'<td style="font-size:.72rem;color:var(--sub)">{mkt_str}</td>'
                f'<td class="tdn">{odds_d}</td>'
                f'<td class="tdn" style="font-family:monospace;color:var(--muted)">{r_icons}</td>'
                f'<td class="tdn" style="color:{icon_c};font-weight:700">{icon}</td>'
                f'<td class="tdn" style="color:{p_col};font-weight:700">{p_str}</td>'
                f'</tr>'
            )
        hist_html = (
            f'<table class="ct" style="margin-top:14px">'
            f'<thead><tr><th>Data</th><th>Picks</th><th>Odds</th><th>Resultado</th>'
            f'<th>Win</th><th>Lucro</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
        )
    else:
        hist_html = '<p class="nd" style="margin-top:12px">Sem triplas pontuadas ainda.</p>'

    css_extra = (
        '.tb-today{background:#0d1e35;border:1px solid #1e4d8c;border-radius:12px;padding:16px;margin-bottom:16px}'
        '.tb-today-hdr{display:flex;justify-content:space-between;align-items:center;'
        'font-size:.82rem;font-weight:700;color:#60a5fa;margin-bottom:12px}'
        '.tb-odds{font-size:.78rem;color:var(--sub)}.tb-odds b{color:var(--text)}'
        '.tp{display:flex;align-items:center;gap:10px;padding:8px 0;'
        'border-bottom:1px solid #1a2540}'
        '.tp:last-of-type{border-bottom:none}'
        '.tpn{width:20px;height:20px;border-radius:50%;background:#1e3a5f;color:#60a5fa;'
        'font-size:.68rem;font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0}'
        '.tpi{flex:1;min-width:0}'
        '.tpl{font-size:.65rem;color:var(--muted);margin-bottom:2px}'
        '.tpm{font-size:.82rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}'
        '.tpr{display:flex;gap:8px;align-items:center;flex-shrink:0}'
        '.tpk{font-size:.72rem;color:var(--sub)}'
        '.tpo{font-size:.72rem;color:var(--muted)}'
        '.tb-note{font-size:.68rem;color:var(--muted);margin-top:10px;font-style:italic}'
        '.tb-empty{color:var(--muted);font-style:italic;font-size:.82rem;padding:10px 0}'
        '.tb-roi{display:flex;gap:0;border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-bottom:16px}'
        '.tb-roi-item{flex:1;padding:14px;text-align:center;border-right:1px solid var(--border)}'
        '.tb-roi-item:last-child{border-right:none}'
        '.tb-roi-n{font-size:1.4rem;font-weight:800;line-height:1}'
        '.tb-roi-l{font-size:.62rem;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-top:4px}'
    )

    return css_extra, (
        f'<div class="stitle">Triplas de Apostas</div>'
        f'<div class="sc" style="margin-bottom:28px">'
        f'{roi_html}'
        f'{today_html}'
        f'{hist_html}'
        f'</div>'
    )

def build_html(history, trebles_data=None):
    records   = history.get("records",[])
    processed = history.get("dates_processed",[])
    now       = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total     = len(records)
    date_min  = min((r["date"] for r in records),default="–")
    date_max  = max((r["date"] for r in records),default="–")

    s1  = calc_stats(records,"pick_1x2","hit_1x2","1X2 (MÉDIA)")
    s2  = calc_stats(records,"pick_o25","hit_o25","Over 2.5 (xG≥2.9)")
    s3  = calc_stats(records,"pick_btts","hit_btts","BTTS (ALTA+MÉDIA)")
    sxg = calc_xg(records)
    tl1 = top_leagues(records,"pick_1x2","hit_1x2")
    tl2 = top_leagues(records,"pick_o25","hit_o25")
    tl3 = top_leagues(records,"pick_btts","hit_btts")

    treble_css  = ""
    treble_body = ""
    if trebles_data:
        treble_css, treble_body = treble_section_html(trebles_data)

    xga_css, xga_body = xg_analysis_html(records)

    def stat_card(s):
        col = rc(s["rate"])
        bw  = int(s["rate"])
        conf_rows = "".join(
            f'<tr><td class="tdc">{c}</td><td>{cv["picks"]}</td>'
            f'<td>{cv["hits"]}</td><td style="color:{rc(cv["rate"])};font-weight:700">{cv["rate"]}%</td></tr>'
            for c,cv in s.get("by_conf",{}).items()
        )
        trend = s.get("trend",[])
        thtml = ""
        if trend:
            mx = max(t["rate"] for t in trend) or 1
            bars = "".join(
                f'<div class="tb" style="height:{max(4,int(t["rate"]/mx*48))}px;background:{rc(t["rate"])}" title="{t["w"]}: {t["rate"]}%"></div>'
                for t in trend
            )
            thtml = f'<div class="tw"><div class="tlbl">Tendência semanal</div><div class="tbars">{bars}</div></div>'
        return (
            f'<div class="sc">'
            f'<div class="sc-top"><div><div class="sc-title">{s["label"]}</div>'
            f'<div class="sc-sub">{s["picks"]} picks · {s["hits"]} acertos</div></div>'
            f'<div class="sc-rate" style="color:{col}">{s["rate"]}%</div></div>'
            f'<div class="rbg"><div class="rf" style="width:{bw}%;background:{col}"></div></div>'
            f'{thtml}'
            f'<table class="ct"><thead><tr><th>Confiança</th><th>Picks</th><th>Acertos</th><th>Taxa</th></tr></thead>'
            f'<tbody>{conf_rows or "<tr><td colspan=4 style=color:#4a5568>Ainda sem dados</td></tr>"}</tbody></table>'
            f'</div>'
        )

    xg_card = (
        f'<div class="sc">'
        f'<div class="sc-top"><div><div class="sc-title">xG vs Golos Reais</div>'
        f'<div class="sc-sub">{sxg["picks"]} jogos</div></div></div>'
        f'<div class="xgg">'
        f'<div class="xgi"><div class="xgv">{sxg["avg_xg"]}</div><div class="xgl">xG médio previsto</div></div>'
        f'<div class="xgi"><div class="xgv" style="color:#60a5fa">{sxg["avg_goals"]}</div><div class="xgl">Golos médios reais</div></div>'
        f'<div class="xgi"><div class="xgv" style="color:#fbbf24">{sxg["over_rate"]}%</div><div class="xgl">Golos &gt; xG</div></div>'
        f'</div></div>'
    )

    def lt(rows, title):
        if not rows:
            return f'<div class="lc"><div class="lct">{title}</div><p class="nd">Mín. 5 picks necessários</p></div>'
        trs = "".join(
            f'<tr><td class="tdl">{r["league"]}</td><td class="tdn">{r["picks"]}</td>'
            f'<td class="tdn" style="color:{rc(r["rate"])};font-weight:700">{r["rate"]}%</td></tr>'
            for r in rows
        )
        return (
            f'<div class="lc"><div class="lct">{title}</div>'
            f'<table class="ct"><thead><tr><th>Liga</th><th>Picks</th><th>Taxa</th></tr></thead>'
            f'<tbody>{trs}</tbody></table></div>'
        )

    empty = total == 0
    if empty:
        body = (
            '<div class="empty"><div class="ebig">🔬</div>'
            '<p>O backtest ainda não tem dados históricos.<br>'
            'As predições de hoje estão a ser guardadas automaticamente.<br><br>'
            'Os primeiros resultados aparecem amanhã de manhã.</p></div>'
        )
    else:
        body = (
            f'{treble_body}'
            f'<div class="info">'
            f'<span>📅 <b>{date_min}</b> → <b>{date_max}</b></span>'
            f'<span>🎯 Jogos: <b>{total}</b></span>'
            f'<span>📆 Dias: <b>{len(processed)}</b></span>'
            f'<span class="grow">A crescer diariamente ↑</span>'
            f'</div>'
            f'<div class="stitle">Taxa de Acerto por Mercado</div>'
            f'<div class="grid">{stat_card(s1)}{stat_card(s2)}{stat_card(s3)}{xg_card}</div>'
            f'<div class="stitle">Top Ligas (mín. 5 picks)</div>'
            f'<div class="lgrid">{lt(tl1,"1X2")}{lt(tl2,"Over 2.5")}{lt(tl3,"BTTS")}</div>'
            f'{xga_body}'
        )

    css = (
        ':root{--bg:#0d1117;--card:#1c2333;--border:#2d3748;--blue:#60a5fa;--green:#4ade80;'
        '--yellow:#fbbf24;--red:#f87171;--text:#f1f5f9;--sub:#94a3b8;--muted:#4a5568}'
        '*{box-sizing:border-box;margin:0;padding:0}body{background:var(--bg);color:var(--text);'
        'font-family:"Inter","Segoe UI",system-ui,sans-serif}'
        '.hdr{background:linear-gradient(180deg,#0a0f1e,#0d1117);border-bottom:1px solid var(--border);padding:20px 28px}'
        '.hdr h1{font-size:1.5rem;font-weight:800;background:linear-gradient(90deg,#60a5fa,#a78bfa);'
        '-webkit-background-clip:text;-webkit-text-fill-color:transparent}'
        '.hdr .meta{font-size:.72rem;color:var(--muted);margin-top:4px}'
        '.tabs{display:flex;background:#0a0f1e;border-bottom:1px solid var(--border);padding:0 28px}'
        '.tab{padding:12px 20px;font-size:.82rem;font-weight:600;color:var(--muted);'
        'border-bottom:2px solid transparent;text-decoration:none;transition:all .15s}'
        '.tab:hover{color:var(--sub)}.tab.active{color:var(--blue);border-bottom-color:var(--blue)}'
        '.wrap{max-width:960px;margin:0 auto;padding:24px 28px}'
        '.info{background:#161b27;border:1px solid var(--border);border-radius:10px;padding:14px 20px;'
        'margin-bottom:24px;display:flex;gap:20px;flex-wrap:wrap;align-items:center;font-size:.8rem;color:var(--sub)}'
        '.info b{color:var(--text)}.grow{margin-left:auto;font-size:.72rem;color:var(--muted)}'
        '.stitle{font-size:.85rem;font-weight:700;color:var(--sub);margin:0 0 14px;text-transform:uppercase;'
        'letter-spacing:.5px;padding-left:10px;border-left:3px solid var(--blue)}'
        '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-bottom:28px}'
        '.lgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}'
        '.sc,.lc{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px}'
        '.sc-top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px}'
        '.sc-title{font-size:1rem;font-weight:700}.sc-sub{font-size:.72rem;color:var(--muted);margin-top:3px}'
        '.sc-rate{font-size:1.8rem;font-weight:800;line-height:1}'
        '.rbg{height:5px;background:var(--border);border-radius:3px;margin-bottom:14px}'
        '.rf{height:100%;border-radius:3px}'
        '.tw{margin-bottom:12px}.tlbl{font-size:.65rem;color:var(--muted);text-transform:uppercase;'
        'letter-spacing:.4px;margin-bottom:6px}'
        '.tbars{display:flex;align-items:flex-end;gap:4px;height:52px}'
        '.tb{flex:1;border-radius:3px 3px 0 0;min-width:8px;cursor:pointer}'
        '.ct{width:100%;border-collapse:collapse;font-size:.78rem;margin-top:10px}'
        '.ct th{text-align:left;color:var(--muted);padding:5px 6px;font-size:.65rem;text-transform:uppercase;'
        'letter-spacing:.4px;border-bottom:1px solid var(--border)}'
        '.ct td{padding:6px 6px;border-bottom:1px solid #1a1f2e}.ct tr:last-child td{border-bottom:none}'
        '.tdc{color:var(--sub)}.tdl{color:var(--text);max-width:160px;overflow:hidden;'
        'text-overflow:ellipsis;white-space:nowrap}.tdn{text-align:right}'
        '.xgg{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:14px 0}'
        '.xgi{background:#0f1420;border:1px solid var(--border);border-radius:8px;padding:12px;text-align:center}'
        '.xgv{font-size:1.3rem;font-weight:800;color:var(--green)}.xgl{font-size:.65rem;color:var(--muted);margin-top:4px}'
        '.lct{font-size:.78rem;font-weight:700;color:var(--sub);margin-bottom:12px;text-transform:uppercase;letter-spacing:.4px}'
        '.nd{font-size:.78rem;color:var(--muted);font-style:italic;padding:8px 0}'
        '.empty{text-align:center;padding:60px 20px;color:var(--muted)}.ebig{font-size:3rem;margin-bottom:12px}'
        '.empty p{font-size:.88rem;line-height:1.8}'
        '.footer{text-align:center;padding:28px;font-size:.68rem;color:var(--muted);border-top:1px solid var(--border)}'
        '@media(max-width:580px){.wrap,.hdr{padding-left:14px;padding-right:14px}'
        '.xga-grid2{grid-template-columns:1fr}}'
        + treble_css + xga_css
    )

    return (
        f'<!DOCTYPE html><html lang="pt"><head><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Matemática Da Bola — Backtest</title>'
        f'<script async src="https://www.googletagmanager.com/gtag/js?id=G-WE48R4KL96"></script>'
        f'<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments)}}'
        f'gtag("js",new Date());gtag("config","G-WE48R4KL96");</script>'
        f'<style>{css}</style></head><body>'
        f'<div class="hdr"><h1>⚽ Matemática Da Bola</h1>'
        f'<div class="meta">Backtest actualizado em {now}</div></div>'
        f'<div class="tabs">'
        f'<a href="dashboard.html" class="tab">📊 Dashboard</a>'
        f'<a href="backtest.html" class="tab active">🔬 Backtest</a>'
        f'</div>'
        f'<div class="wrap">{body}</div>'
        f'<div class="footer">Matemática Da Bola · Backtest · Dados desde {date_min}</div>'
        f'</body></html>'
    )

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today = today_str()
    os.makedirs("docs", exist_ok=True)

    history = load_history()
    trebles = load_trebles()

    # Migrar todos os registos existentes para os novos thresholds de pick
    history["records"] = migrate_picks(history.get("records", []))

    # ── MODO SAVE ────────────────────────────────────────────────────────────
    save_file = preds_file(today)
    if not os.path.exists(save_file):
        print(f"[backtest] SAVE: a guardar predições de {today}...")
        preds = fetch_todays_predictions()
        print(f"[backtest] {len(preds)} predições para {today}")
        if preds:
            with open(save_file, "w", encoding="utf-8") as f:
                json.dump(preds, f, ensure_ascii=False, separators=(",", ":"))
            print(f"[backtest] {save_file} guardado ✓")
    else:
        print(f"[backtest] SAVE: {save_file} já existe — a saltar")

    # Tentar construir tripla do dia (da preds file actual ou existente)
    today_built = any(
        t.get("date") == today
        for t in trebles.get("pending", []) + trebles.get("history", [])
    )
    if not today_built and os.path.exists(save_file):
        try:
            with open(save_file, encoding="utf-8") as f:
                preds = json.load(f)
            treble = build_daily_treble(preds)
            if treble:
                trebles.setdefault("pending", []).append(treble)
                odds_str = f"{treble['combined_odds']:.2f}" if treble.get("combined_odds") else "?"
                print(f"[backtest] Tripla do dia: {len(treble['picks'])} picks, odds={odds_str} ✓")
            else:
                print("[backtest] Picks insuficientes para tripla hoje")
            save_trebles(trebles)
        except Exception as e:
            print(f"[backtest] Erro ao construir tripla: {e}")

    # ── MODO SCORE ───────────────────────────────────────────────────────────
    processed = history.get("dates_processed", [])

    all_pred_files = [
        f for f in os.listdir("docs")
        if f.startswith("preds_") and f.endswith(".json")
    ]
    pending_dates = []
    for fname in sorted(all_pred_files):
        date_str = fname.replace("preds_","").replace(".json","")
        if date_str == today:
            continue
        if date_str in processed:
            continue
        pending_dates.append(date_str)

    print(f"[backtest] Datas pendentes para SCORE: {pending_dates or 'nenhuma'}")

    new_records_total = 0
    for date_str in pending_dates:
        yfile = preds_file(date_str)
        print(f"[backtest] SCORE: a processar {date_str}...")
        try:
            with open(yfile, encoding="utf-8") as f:
                preds = json.load(f)
        except Exception as e:
            print(f"[backtest] SCORE: erro a ler {yfile}: {e}")
            continue

        print(f"[backtest] {len(preds)} predições carregadas de {date_str}")
        new_records = []
        found = 0
        not_finished = 0

        for p in preds:
            eid = p.get("event", {}).get("id")
            if not eid:
                continue
            result = fetch_event_result(eid)
            if result:
                found += 1
                new_records.append(make_record(p, result))
            else:
                not_finished += 1

        print(f"[backtest] {found}/{len(preds)} com resultado | {not_finished} sem resultado")

        coverage = found / len(preds) if preds else 0
        if coverage >= 0.7:
            history["records"] = [r for r in history.get("records",[]) if r.get("date") != date_str]
            history["records"].extend(new_records)
            history["dates_processed"] = list(set(processed + [date_str]))
            processed = history["dates_processed"]
            new_records_total += len(new_records)
            print(f"[backtest] {date_str} marcado como processado ({coverage:.0%} cobertura)")
        else:
            history["records"] = [r for r in history.get("records",[]) if r.get("date") != date_str]
            history["records"].extend(new_records)
            history.setdefault("dates_partial", {})[date_str] = found
            print(f"[backtest] {date_str} parcial ({coverage:.0%}) — tentará de novo amanhã")

        save_history(history)

    if new_records_total > 0:
        print(f"[backtest] Total acumulado: {len(history['records'])} jogos")

    # Pontuar triplas pendentes cujas datas já foram processadas
    still_pending = []
    for treble in trebles.get("pending", []):
        t_date = treble.get("date", "")
        if t_date in history.get("dates_processed", []):
            recs_for_date = [r for r in history.get("records", []) if r.get("date") == t_date]
            scored = score_treble(treble, recs_for_date)
            if scored:
                trebles.setdefault("history", []).append(scored)
                result_str = "✓ GANHOU" if scored["hit"] else "✗ PERDEU"
                odds_str   = f"{treble['combined_odds']:.2f}" if treble.get("combined_odds") else "?"
                print(f"[backtest] Tripla {t_date}: {result_str} (odds={odds_str})")
            else:
                still_pending.append(treble)
        else:
            still_pending.append(treble)

    trebles["pending"] = still_pending
    save_trebles(trebles)

    # Gerar HTML com secção de triplas
    html = build_html(history, trebles)
    with open("docs/backtest.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("[backtest] docs/backtest.html gerado ✓")

if __name__ == "__main__":
    main()
