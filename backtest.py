"""
Matemática Da Bola — Backtest v2 (fixed)
Modo SAVE  (07:00/14:00/21:00 UTC): guarda predições do dia em docs/preds_YYYY-MM-DD.json
Modo SCORE (00:00 UTC + sempre): cruza predições de dias anteriores com resultados reais

FIXES:
- BUG 2: SCORE agora tenta processar TODOS os dias não processados, não só ontem
- BUG 4: parse seguro de datas ISO (Z suffix)
- Robusto a falhas parciais: dias com poucos resultados são reprocessados no dia seguinte
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

def get(path, params=None):
    r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def yesterday_str():
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

# FIX BUG 4: parse seguro de datas
def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def preds_file(date_str):
    return f"docs/preds_{date_str}.json"

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

def make_record(pred, result):
    event   = pred.get("event", {})
    markets = pred.get("markets", {})
    mr      = markets.get("match_result", {})
    ou      = markets.get("over_under", {})
    bt      = markets.get("btts", {})
    xg      = markets.get("expected_goals", {})
    model   = pred.get("model", {})

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

    # FIX: usar parse_dt para datas
    event_date = event.get("event_date", "")
    dt = parse_dt(event_date)
    date_str = dt.strftime("%Y-%m-%d") if dt else event_date[:10]

    return {
        "date":     date_str,
        "league":   event.get("league_name", "?"),
        "home":     event.get("home_team", "?"),
        "away":     event.get("away_team", "?"),
        "hs": hs, "as": as_, "goals": goals,
        "ph": round(ph,1), "pd": round(pd,1), "pa": round(pa,1),
        "po": round(po,1), "pb": round(pb,1), "xg": xgt,
        "conf": conf,
        "pred": pred_r, "real": real,
        "pick_1x2":  best >= 61,
        "pick_o25":  po >= 61,
        "pick_btts": pb >= 61,
        "pick_xg":   xgt > 0,
        "hit_1x2":   pred_r == real,
        "hit_o25":   goals > 2,
        "hit_btts":  hs > 0 and as_ > 0,
    }

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
        except Exception: pass
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

def build_html(history):
    records   = history.get("records",[])
    processed = history.get("dates_processed",[])
    now       = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total     = len(records)
    date_min  = min((r["date"] for r in records),default="–")
    date_max  = max((r["date"] for r in records),default="–")

    s1  = calc_stats(records,"pick_1x2","hit_1x2","1X2")
    s2  = calc_stats(records,"pick_o25","hit_o25","Over 2.5")
    s3  = calc_stats(records,"pick_btts","hit_btts","BTTS")
    sxg = calc_xg(records)
    tl1 = top_leagues(records,"pick_1x2","hit_1x2")
    tl2 = top_leagues(records,"pick_o25","hit_o25")
    tl3 = top_leagues(records,"pick_btts","hit_btts")

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
        )

    # CSS inline (mesmo do original, condensado)
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
        '@media(max-width:580px){.wrap,.hdr{padding-left:14px;padding-right:14px}}'
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

def main():
    today     = today_str()
    yesterday = yesterday_str()
    now_utc   = datetime.now(timezone.utc)

    os.makedirs("docs", exist_ok=True)
    history = load_history()

    # ── MODO SAVE: guardar predições de hoje ─────────────────────
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

    # ── FIX BUG 2: SCORE tenta processar TODOS os dias pendentes ─
    # Não apenas ontem — procura todos os preds_*.json não processados
    processed = history.get("dates_processed", [])
    partial   = history.get("dates_partial", {})

    # Listar todos os ficheiros preds_*.json disponíveis
    all_pred_files = [
        f for f in os.listdir("docs")
        if f.startswith("preds_") and f.endswith(".json")
    ]
    pending_dates = []
    for fname in sorted(all_pred_files):
        date_str = fname.replace("preds_","").replace(".json","")
        if date_str == today:
            continue  # hoje ainda não terminou
        if date_str in processed:
            continue  # já processado com sucesso
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

        # Só marcar como processado se encontrámos a maioria dos resultados
        # (evita marcar como done se a BSD ainda não tem os scores)
        coverage = found / len(preds) if preds else 0
        if coverage >= 0.7:
            # Remover registos antigos deste dia (para não duplicar em re-runs)
            history["records"] = [r for r in history.get("records",[]) if r.get("date") != date_str]
            history["records"].extend(new_records)
            history["dates_processed"] = list(set(processed + [date_str]))
            processed = history["dates_processed"]
            new_records_total += len(new_records)
            print(f"[backtest] {date_str} marcado como processado ({coverage:.0%} cobertura)")
        else:
            # Guardar parcialmente mas tentar de novo amanhã
            history["records"] = [r for r in history.get("records",[]) if r.get("date") != date_str]
            history["records"].extend(new_records)
            history.setdefault("dates_partial", {})[date_str] = found
            print(f"[backtest] {date_str} parcial ({coverage:.0%}) — tentará de novo amanhã")

        save_history(history)

    if new_records_total > 0:
        print(f"[backtest] Total acumulado: {len(history['records'])} jogos")

    # Gerar HTML sempre
    html = build_html(history)
    with open("docs/backtest.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("[backtest] docs/backtest.html gerado ✓")

if __name__ == "__main__":
    main()
