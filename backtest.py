"""
Matemática Da Bola — Backtest
Corre diariamente, busca jogos de ontem com resultados reais,
acumula em docs/history.json e gera docs/backtest.html
"""

import os
import json
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

BSD_KEY = os.environ["BSD_API_KEY"]
BASE    = "https://sports.bzzoiro.com/api/v2"
HEADERS = {"Authorization": f"Token {BSD_KEY}"}
HISTORY_FILE = "docs/history.json"

def get(path, params=None):
    r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def yesterday_str():
    d = datetime.now(timezone.utc) - timedelta(days=1)
    return d.strftime("%Y-%m-%d")

def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ── Carregar histórico existente ──────────────────────────────────────────────
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"records": [], "dates_processed": []}

def save_history(history):
    os.makedirs("docs", exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",", ":"))

# ── Fetch predições de uma data ───────────────────────────────────────────────
def fetch_predictions_for_date(date_str):
    """Busca predicoes de uma data especifica com resultados finais.
    Aceita jogos com home_score/away_score preenchidos, independente do status field."""
    all_preds = []
    offset = 0
    limit  = 50

    while True:
        try:
            data = get("/predictions/", {
                "limit":  limit,
                "offset": offset,
            })
            results = data.get("results", [])
            for r in results:
                event = r.get("event", {})
                ed    = event.get("event_date", "")[:10]
                hs    = event.get("home_score")
                as_   = event.get("away_score")
                # Aceita se tem resultado E é da data certa
                if ed == date_str and hs is not None and as_ is not None:
                    all_preds.append(r)

            if not data.get("next"):
                break
            # Se já passámos da data que queremos (BSD ordena por data desc), parar
            dates_in_page = [r.get("event",{}).get("event_date","")[:10] for r in results]
            if dates_in_page and all(d < date_str for d in dates_in_page if d):
                print(f"  [bt] Passou da data {date_str} — a parar paginação")
                break
            offset += limit
        except Exception as e:
            print(f"  [WARN] offset={offset}: {e}")
            break

    return all_preds

# ── Converter predição em registo ─────────────────────────────────────────────
def pred_to_record(pred):
    event   = pred.get("event", {})
    markets = pred.get("markets", {})
    mr      = markets.get("match_result", {})
    ou      = markets.get("over_under", {})
    bt      = markets.get("btts", {})
    xg      = markets.get("expected_goals", {})
    model   = pred.get("model", {})

    home_score  = int(event.get("home_score", 0))
    away_score  = int(event.get("away_score", 0))
    total_goals = home_score + away_score

    prob_home = float(mr.get("prob_home") or 0)
    prob_draw = float(mr.get("prob_draw") or 0)
    prob_away = float(mr.get("prob_away") or 0)
    prob_o25  = float(ou.get("prob_over_25") or 0)
    prob_btts = float(bt.get("prob_yes") or 0)
    xg_home   = float(xg.get("home") or 0)
    xg_away   = float(xg.get("away") or 0)
    xg_total  = round(xg_home + xg_away, 2)
    confidence= float(model.get("confidence") or 0)

    # Resultado real
    if home_score > away_score:   real = "H"
    elif home_score == away_score: real = "D"
    else:                          real = "A"

    # Previsão ML
    best = max(prob_home, prob_draw, prob_away)
    if best == prob_home:   pred_r = "H"
    elif best == prob_draw: pred_r = "D"
    else:                   pred_r = "A"

    # Nível de confiança
    if confidence >= 0.65:   conf = "ALTA"
    elif confidence >= 0.45: conf = "MÉDIA"
    else:                     conf = "BAIXA"

    return {
        "date":        event.get("event_date", "")[:10],
        "league":      event.get("league_name", "?"),
        "home":        event.get("home_team", "?"),
        "away":        event.get("away_team", "?"),
        "home_score":  home_score,
        "away_score":  away_score,
        "goals":       total_goals,
        # Probabilidades
        "p_home":  round(prob_home, 1),
        "p_draw":  round(prob_draw, 1),
        "p_away":  round(prob_away, 1),
        "p_o25":   round(prob_o25, 1),
        "p_btts":  round(prob_btts, 1),
        "xg":      xg_total,
        "conf":    conf,
        # Previsões
        "pred":    pred_r,
        "real":    real,
        # Picks activos (≥61%)
        "pick_1x2":  best >= 61,
        "pick_o25":  prob_o25 >= 61,
        "pick_btts": prob_btts >= 61,
        "pick_xg":   xg_total > 0,
        # Resultados
        "hit_1x2":  pred_r == real,
        "hit_o25":  total_goals > 2,
        "hit_btts": home_score > 0 and away_score > 0,
    }

# ── Estatísticas ──────────────────────────────────────────────────────────────
def calc_stats(records, pick_key, hit_key, label):
    subset = [r for r in records if r.get(pick_key)]
    if not subset:
        return {"label": label, "picks": 0, "hits": 0, "rate": 0.0, "by_conf": {}, "trend": []}

    hits = sum(1 for r in subset if r.get(hit_key))
    rate = round(hits / len(subset) * 100, 1)

    by_conf = {}
    for conf in ["ALTA", "MÉDIA", "BAIXA"]:
        sub = [r for r in subset if r["conf"] == conf]
        if sub:
            h = sum(1 for r in sub if r.get(hit_key))
            by_conf[conf] = {
                "picks": len(sub),
                "hits":  h,
                "rate":  round(h / len(sub) * 100, 1),
            }

    # Tendência semanal (últimas 4 semanas)
    from collections import defaultdict
    weekly = defaultdict(lambda: {"picks": 0, "hits": 0})
    for r in subset:
        try:
            d = datetime.fromisoformat(r["date"])
            # Número da semana ISO
            wk = d.strftime("%Y-W%V")
            weekly[wk]["picks"] += 1
            if r.get(hit_key):
                weekly[wk]["hits"] += 1
        except Exception:
            pass
    trend = [
        {"week": wk, "rate": round(v["hits"]/v["picks"]*100, 1), "picks": v["picks"]}
        for wk, v in sorted(weekly.items())[-8:]
        if v["picks"] >= 3
    ]

    return {
        "label":   label,
        "picks":   len(subset),
        "hits":    hits,
        "rate":    rate,
        "by_conf": by_conf,
        "trend":   trend,
    }

def calc_xg_stats(records):
    subset = [r for r in records if r.get("pick_xg") and r.get("xg", 0) > 0]
    if not subset:
        return {"picks": 0, "avg_xg": 0, "avg_goals": 0, "over_xg_rate": 0}
    avg_xg    = round(sum(r["xg"] for r in subset) / len(subset), 2)
    avg_goals = round(sum(r["goals"] for r in subset) / len(subset), 2)
    over_rate = round(sum(1 for r in subset if r["goals"] > r["xg"]) / len(subset) * 100, 1)
    return {"picks": len(subset), "avg_xg": avg_xg, "avg_goals": avg_goals, "over_xg_rate": over_rate}

def top_leagues(records, pick_key, hit_key, n=8):
    by_league = defaultdict(lambda: {"picks": 0, "hits": 0})
    for r in records:
        if r.get(pick_key):
            by_league[r["league"]]["picks"] += 1
            if r.get(hit_key):
                by_league[r["league"]]["hits"] += 1
    rows = [
        {"league": lg, "picks": v["picks"], "hits": v["hits"],
         "rate": round(v["hits"] / v["picks"] * 100, 1)}
        for lg, v in by_league.items() if v["picks"] >= 5
    ]
    return sorted(rows, key=lambda x: x["rate"], reverse=True)[:n]

# ── HTML ──────────────────────────────────────────────────────────────────────
def rate_color(rate):
    if rate >= 65: return "#4ade80"
    if rate >= 55: return "#fbbf24"
    return "#f87171"

def build_html(history):
    records   = history.get("records", [])
    processed = history.get("dates_processed", [])
    now       = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total     = len(records)

    if not records:
        date_min = date_max = "–"
    else:
        dates    = [r["date"] for r in records]
        date_min = min(dates)
        date_max = max(dates)

    s1x2  = calc_stats(records, "pick_1x2",  "hit_1x2",  "1X2")
    so25  = calc_stats(records, "pick_o25",   "hit_o25",  "Over 2.5")
    sbtts = calc_stats(records, "pick_btts",  "hit_btts", "BTTS")
    sxg   = calc_xg_stats(records)
    tl1   = top_leagues(records, "pick_1x2",  "hit_1x2")
    tl2   = top_leagues(records, "pick_o25",   "hit_o25")
    tl3   = top_leagues(records, "pick_btts",  "hit_btts")

    def stat_block(s):
        rc = rate_color(s["rate"])
        bar_w = int(s["rate"])
        # Conf rows
        conf_rows = ""
        for conf, cv in s.get("by_conf", {}).items():
            cc = rate_color(cv["rate"])
            conf_rows += f"""<tr>
              <td class="td-conf">{conf}</td>
              <td>{cv["picks"]}</td>
              <td>{cv["hits"]}</td>
              <td style="color:{cc};font-weight:700">{cv["rate"]}%</td>
            </tr>"""
        # Trend mini chart
        trend = s.get("trend", [])
        trend_html = ""
        if trend:
            bars = ""
            max_r = max((t["rate"] for t in trend), default=1) or 1
            for t in trend:
                h = max(4, int(t["rate"] / max_r * 48))
                tc = rate_color(t["rate"])
                bars += f'<div class="tb" style="height:{h}px;background:{tc}" title="{t["week"]}: {t["rate"]}% ({t["picks"]} picks)"></div>'
            trend_html = f'<div class="trend-wrap"><div class="trend-label">Tendência semanal</div><div class="trend-bars">{bars}</div></div>'

        return f"""
        <div class="stat-card">
          <div class="sc-top">
            <div>
              <div class="sc-title">{s["label"]}</div>
              <div class="sc-sub">{s["picks"]} picks · {s["hits"]} acertos</div>
            </div>
            <div class="sc-rate" style="color:{rc}">{s["rate"]}%</div>
          </div>
          <div class="rate-bar-bg"><div class="rate-bar-fill" style="width:{bar_w}%;background:{rc}"></div></div>
          {trend_html}
          <table class="ct">
            <thead><tr><th>Confiança</th><th>Picks</th><th>Acertos</th><th>Taxa</th></tr></thead>
            <tbody>{conf_rows if conf_rows else "<tr><td colspan=4 style=color:#4a5568>Ainda sem dados suficientes</td></tr>"}</tbody>
          </table>
        </div>"""

    def xg_block():
        return f"""
        <div class="stat-card">
          <div class="sc-top">
            <div>
              <div class="sc-title">xG vs Golos Reais</div>
              <div class="sc-sub">{sxg["picks"]} jogos analisados</div>
            </div>
          </div>
          <div class="xg-grid">
            <div class="xg-item"><div class="xg-val">{sxg["avg_xg"]}</div><div class="xg-lbl">xG médio previsto</div></div>
            <div class="xg-item"><div class="xg-val" style="color:#60a5fa">{sxg["avg_goals"]}</div><div class="xg-lbl">Golos médios reais</div></div>
            <div class="xg-item"><div class="xg-val" style="color:#fbbf24">{sxg["over_xg_rate"]}%</div><div class="xg-lbl">Jogos com golos &gt; xG</div></div>
          </div>
        </div>"""

    def league_table(rows, title):
        if not rows:
            return f'<div class="league-card"><div class="lc-title">{title}</div><p class="no-data">Ainda sem dados suficientes (mín. 5 picks)</p></div>'
        trs = "".join(f"""<tr>
          <td class="td-league">{r["league"]}</td>
          <td class="td-num">{r["picks"]}</td>
          <td class="td-num" style="color:{rate_color(r["rate"])};font-weight:700">{r["rate"]}%</td>
        </tr>""" for r in rows)
        return f"""<div class="league-card">
          <div class="lc-title">{title}</div>
          <table class="ct">
            <thead><tr><th>Liga</th><th>Picks</th><th>Taxa</th></tr></thead>
            <tbody>{trs}</tbody>
          </table>
        </div>"""

    # Datas processadas recentes
    recent_dates = ", ".join(sorted(processed)[-7:]) if processed else "–"

    return f"""<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Matemática Da Bola — Backtest</title>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-WE48R4KL96"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments)}}gtag("js",new Date());gtag("config","G-WE48R4KL96");</script>
<style>
:root{{
  --bg:#0d1117;--surface:#161b27;--card:#1c2333;--border:#2d3748;--border-light:#3a4560;
  --blue:#60a5fa;--green:#4ade80;--yellow:#fbbf24;--red:#f87171;--purple:#a78bfa;
  --text:#f1f5f9;--sub:#94a3b8;--muted:#4a5568;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:"Inter","Segoe UI",system-ui,sans-serif}}
.header{{background:linear-gradient(180deg,#0a0f1e,#0d1117);border-bottom:1px solid var(--border);padding:20px 28px}}
.header h1{{font-size:1.5rem;font-weight:800;background:linear-gradient(90deg,#60a5fa,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.header .meta{{font-size:.72rem;color:var(--muted);margin-top:4px}}
.tabs{{display:flex;background:#0a0f1e;border-bottom:1px solid var(--border);padding:0 28px}}
.tab{{padding:12px 20px;font-size:.82rem;font-weight:600;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;text-decoration:none;transition:all .15s}}
.tab:hover{{color:var(--sub)}}
.tab.active{{color:var(--blue);border-bottom-color:var(--blue)}}
.wrap{{max-width:960px;margin:0 auto;padding:24px 28px}}
.info-bar{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 20px;margin-bottom:24px;display:flex;gap:24px;flex-wrap:wrap;align-items:center}}
.info-bar span{{font-size:.8rem;color:var(--sub)}}
.info-bar b{{color:var(--text)}}
.info-bar .growing{{font-size:.72rem;color:var(--muted);margin-left:auto}}
.section-title{{font-size:.9rem;font-weight:700;color:var(--sub);margin:28px 0 14px;text-transform:uppercase;letter-spacing:.5px;padding-left:10px;border-left:3px solid var(--blue)}}
.cards-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-bottom:8px}}
.stat-card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px}}
.sc-top{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px}}
.sc-title{{font-size:1rem;font-weight:700}}
.sc-sub{{font-size:.72rem;color:var(--muted);margin-top:3px}}
.sc-rate{{font-size:1.8rem;font-weight:800;line-height:1}}
.rate-bar-bg{{height:5px;background:var(--border);border-radius:3px;margin-bottom:14px}}
.rate-bar-fill{{height:100%;border-radius:3px;transition:width .5s}}
.trend-wrap{{margin-bottom:12px}}
.trend-label{{font-size:.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px}}
.trend-bars{{display:flex;align-items:flex-end;gap:4px;height:52px}}
.tb{{flex:1;border-radius:3px 3px 0 0;min-width:8px;cursor:pointer;transition:opacity .15s}}
.tb:hover{{opacity:.8}}
.ct{{width:100%;border-collapse:collapse;font-size:.78rem;margin-top:10px}}
.ct th{{text-align:left;color:var(--muted);padding:5px 6px;font-size:.65rem;text-transform:uppercase;letter-spacing:.4px;border-bottom:1px solid var(--border)}}
.ct td{{padding:6px 6px;border-bottom:1px solid #1a1f2e}}
.ct tr:last-child td{{border-bottom:none}}
.td-conf{{color:var(--sub)}}
.xg-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:14px 0}}
.xg-item{{background:#0f1420;border:1px solid var(--border);border-radius:8px;padding:12px;text-align:center}}
.xg-val{{font-size:1.3rem;font-weight:800;color:var(--green)}}
.xg-lbl{{font-size:.65rem;color:var(--muted);margin-top:4px}}
.leagues-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}
.league-card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px}}
.lc-title{{font-size:.78rem;font-weight:700;color:var(--sub);margin-bottom:12px;text-transform:uppercase;letter-spacing:.4px}}
.td-league{{color:var(--text);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.td-num{{text-align:right}}
.no-data{{font-size:.78rem;color:var(--muted);font-style:italic;padding:8px 0}}
.empty-state{{text-align:center;padding:60px 20px;color:var(--muted)}}
.empty-state .big{{font-size:3rem;margin-bottom:12px}}
.empty-state p{{font-size:.88rem;line-height:1.6}}
.footer{{text-align:center;padding:28px;font-size:.68rem;color:var(--muted);border-top:1px solid var(--border)}}
@media(max-width:580px){{.wrap,.header{{padding-left:14px;padding-right:14px}}}}
</style>
</head>
<body>
<div class="header">
  <h1>⚽ Matemática Da Bola</h1>
  <div class="meta">Backtest actualizado em {now}</div>
</div>
<div class="tabs">
  <a href="dashboard.html" class="tab">📊 Dashboard</a>
  <a href="backtest.html"  class="tab active">🔬 Backtest</a>
</div>
<div class="wrap">
  {"" if total > 0 else '''<div class="empty-state"><div class="big">🔬</div><p>O backtest ainda não tem dados históricos.<br>Os resultados de ontem serão adicionados amanhã automaticamente.<br><br>Volta aqui daqui a alguns dias para ver as primeiras estatísticas.</p></div>'''}
  {f"""
  <div class="info-bar">
    <span>📅 Período: <b>{date_min}</b> → <b>{date_max}</b></span>
    <span>🎯 Jogos analisados: <b>{total}</b></span>
    <span>📆 Dias com dados: <b>{len(processed)}</b></span>
    <span class="growing">A crescer diariamente ↑</span>
  </div>
  <div class="section-title">Taxa de Acerto por Mercado</div>
  <div class="cards-grid">
    {stat_block(s1x2)}
    {stat_block(so25)}
    {stat_block(sbtts)}
    {xg_block()}
  </div>
  <div class="section-title">Top Ligas por Taxa de Acerto (mín. 5 picks)</div>
  <div class="leagues-grid">
    {league_table(tl1, "1X2")}
    {league_table(tl2, "Over 2.5")}
    {league_table(tl3, "BTTS")}
  </div>
  """ if total > 0 else ""}
</div>
<div class="footer">Matemática Da Bola · Backtest · Dados acumulados desde {date_min}</div>
</body>
</html>"""

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    yesterday = yesterday_str()
    print(f"[backtest] A processar {yesterday}...")

    history = load_history()
    processed = history.get("dates_processed", [])

    # Não reprocessar datas já tratadas
    if yesterday in processed:
        print(f"[backtest] {yesterday} já processado — a regenerar HTML apenas")
    else:
        preds = fetch_predictions_for_date(yesterday)
        print(f"[backtest] {len(preds)} jogos com resultado final em {yesterday}")

        if preds:
            new_records = [pred_to_record(p) for p in preds]
            history["records"] = history.get("records", []) + new_records
            history["dates_processed"] = processed + [yesterday]
            save_history(history)
            print(f"[backtest] {len(new_records)} registos adicionados ao histórico")
            print(f"[backtest] Total acumulado: {len(history['records'])} jogos")
        else:
            print(f"[backtest] Sem jogos finalizados em {yesterday} (pode ser normal)")
            # Marca como processado mesmo sem dados para não tentar de novo
            history["dates_processed"] = processed + [yesterday]
            save_history(history)

    # Gerar HTML sempre
    html = build_html(history)
    os.makedirs("docs", exist_ok=True)
    with open("docs/backtest.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("[backtest] docs/backtest.html gerado ✓")

if __name__ == "__main__":
    main()
