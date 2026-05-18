"""
Matemática Da Bola — Backtest
Busca TODO o histórico disponível na BSD API (predições + resultados finais)
Gera docs/backtest.html com análise por filtro
"""

import os
import json
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

BSD_KEY  = os.environ["BSD_API_KEY"]
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT  = os.environ.get("TG_CHAT_ID", "")

BASE    = "https://sports.bzzoiro.com/api/v2"
HEADERS = {"Authorization": f"Token {BSD_KEY}"}

def get(path, params=None):
    r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ── Fetch histórico ───────────────────────────────────────────────────────────
def fetch_finished_predictions():
    """Busca todo o histórico de predições e filtra as que têm resultado final."""
    all_preds = []
    offset = 0
    limit  = 50
    pages  = 0
    max_pages = 300  # até 15k registos

    while pages < max_pages:
        try:
            # Sem filtro de status — a BSD pode não suportar esse parâmetro
            data = get("/predictions/", {
                "limit":  limit,
                "offset": offset,
            })
            results = data.get("results", [])
            if not results:
                break

            # Filtrar jogos com resultado real disponível
            finished = [
                r for r in results
                if r.get("event", {}).get("home_score") is not None
                and r.get("event", {}).get("away_score") is not None
                and r.get("event", {}).get("status") == "finished"
            ]
            all_preds.extend(finished)
            print(f"  [bt] offset={offset} -> {len(results)} total, {len(finished)} com resultado")

            # Se não há mais páginas ou todos são sem resultado (só predições futuras), parar
            if not data.get("next"):
                break
            # Se esta página não tinha nenhum resultado, provável que estamos em jogos futuros
            if len(finished) == 0 and pages > 5:
                print(f"  [bt] Sem mais resultados históricos — a parar")
                break

            offset += limit
            pages  += 1
        except Exception as e:
            print(f"  [WARN] offset={offset}: {e}")
            break

    return all_preds

# ── Avaliação de cada predição ────────────────────────────────────────────────
def evaluate(pred):
    """Retorna dict com métricas de cada filtro para este jogo."""
    event   = pred.get("event", {})
    markets = pred.get("markets", {})
    mr      = markets.get("match_result", {})
    ou      = markets.get("over_under", {})
    bt      = markets.get("btts", {})
    xg      = markets.get("expected_goals", {})
    score   = markets.get("score", {})
    model   = pred.get("model", {})

    home_score = int(event.get("home_score", 0))
    away_score = int(event.get("away_score", 0))
    total_goals = home_score + away_score

    # Probabilidades ML (0-100)
    prob_home  = float(mr.get("prob_home") or 0)
    prob_draw  = float(mr.get("prob_draw") or 0)
    prob_away  = float(mr.get("prob_away") or 0)
    prob_o25   = float(ou.get("prob_over_25") or 0)
    prob_btts  = float(bt.get("prob_yes") or 0)
    xg_home    = float(xg.get("home") or 0)
    xg_away    = float(xg.get("away") or 0)
    xg_total   = round(xg_home + xg_away, 2)
    confidence = float(model.get("confidence") or 0)

    # Resultado real
    if home_score > away_score:
        real_result = "H"
    elif home_score == away_score:
        real_result = "D"
    else:
        real_result = "A"

    real_over25 = total_goals > 2
    real_btts   = home_score > 0 and away_score > 0

    # Previsão ML
    best_prob = max(prob_home, prob_draw, prob_away)
    if best_prob == prob_home:
        pred_result = "H"
    elif best_prob == prob_draw:
        pred_result = "D"
    else:
        pred_result = "A"

    # Confiança
    if confidence >= 0.65:
        conf_label = "ALTA"
    elif confidence >= 0.45:
        conf_label = "MÉDIA"
    else:
        conf_label = "BAIXA"

    return {
        "date":       event.get("event_date", "")[:10],
        "league":     event.get("league_name", "?"),
        "home":       event.get("home_team", "?"),
        "away":       event.get("away_team", "?"),
        "home_score": home_score,
        "away_score": away_score,
        "total_goals": total_goals,
        # Filtros activos (threshold ≥61%)
        "pick_1x2":   best_prob >= 61,
        "pick_o25":   prob_o25  >= 61,
        "pick_btts":  prob_btts >= 61,
        "pick_xg":    xg_total  > 0,
        "conf":       conf_label,
        # Acertos
        "hit_1x2":    pred_result == real_result,
        "hit_o25":    real_over25,
        "hit_btts":   real_btts,
        "xg_total":   xg_total,
        "goals_total": total_goals,
        # Meta
        "prob_1x2":   best_prob,
        "prob_o25":   prob_o25,
        "prob_btts":  prob_btts,
        "pred_result": pred_result,
        "real_result": real_result,
    }

# ── Estatísticas por filtro ───────────────────────────────────────────────────
def calc_stats(records, pick_key, hit_key, label):
    subset = [r for r in records if r.get(pick_key)]
    if not subset:
        return {"label": label, "picks": 0, "hits": 0, "rate": 0, "by_conf": {}}

    hits  = sum(1 for r in subset if r.get(hit_key))
    rate  = hits / len(subset) * 100

    # Por confiança
    by_conf = {}
    for conf in ["ALTA", "MÉDIA", "BAIXA"]:
        sub = [r for r in subset if r.get("conf") == conf]
        if sub:
            h = sum(1 for r in sub if r.get(hit_key))
            by_conf[conf] = {"picks": len(sub), "hits": h, "rate": round(h/len(sub)*100,1)}

    return {
        "label":   label,
        "picks":   len(subset),
        "hits":    hits,
        "rate":    round(rate, 1),
        "by_conf": by_conf,
    }

def calc_xg_stats(records):
    subset = [r for r in records if r.get("pick_xg") and r.get("xg_total", 0) > 0]
    if not subset:
        return {"label": "xG Alto", "picks": 0, "avg_xg": 0, "avg_goals": 0, "over_rate": 0}
    avg_xg    = sum(r["xg_total"] for r in subset) / len(subset)
    avg_goals = sum(r["goals_total"] for r in subset) / len(subset)
    over_rate = sum(1 for r in subset if r["goals_total"] > r["xg_total"]) / len(subset) * 100
    return {
        "label":     "xG Alto",
        "picks":     len(subset),
        "avg_xg":    round(avg_xg, 2),
        "avg_goals": round(avg_goals, 2),
        "over_rate": round(over_rate, 1),
    }

# ── Top ligas ─────────────────────────────────────────────────────────────────
def top_leagues(records, pick_key, hit_key, n=10):
    by_league = defaultdict(lambda: {"picks":0,"hits":0})
    for r in records:
        if r.get(pick_key):
            lg = r["league"]
            by_league[lg]["picks"] += 1
            if r.get(hit_key):
                by_league[lg]["hits"] += 1
    rows = [
        {"league": lg, "picks": v["picks"], "hits": v["hits"],
         "rate": round(v["hits"]/v["picks"]*100, 1)}
        for lg, v in by_league.items() if v["picks"] >= 10
    ]
    return sorted(rows, key=lambda x: x["rate"], reverse=True)[:n]

# ── HTML ──────────────────────────────────────────────────────────────────────
def rate_color(rate):
    if rate >= 65: return "#4ade80"
    if rate >= 55: return "#fbbf24"
    return "#f87171"

def build_backtest_html(records, stats_1x2, stats_o25, stats_btts, stats_xg,
                         top_1x2, top_o25, top_btts):
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = len(records)
    date_min = min((r["date"] for r in records), default="?")
    date_max = max((r["date"] for r in records), default="?")

    def stat_card(s, extra=""):
        rate = s["rate"]
        col  = rate_color(rate)
        rows = ""
        for conf, cv in s.get("by_conf", {}).items():
            cc = rate_color(cv["rate"])
            rows += f'''<tr>
              <td>{conf}</td>
              <td>{cv["picks"]}</td>
              <td>{cv["hits"]}</td>
              <td style="color:{cc};font-weight:700">{cv["rate"]}%</td>
            </tr>'''
        return f'''
        <div class="stat-card">
          <div class="sc-header">
            <span class="sc-title">{s["label"]}</span>
            <span class="sc-rate" style="color:{col}">{rate}%</span>
          </div>
          <div class="sc-sub">{s["picks"]} picks · {s["hits"]} acertos</div>
          {extra}
          <table class="conf-table">
            <thead><tr><th>Confiança</th><th>Picks</th><th>Acertos</th><th>Taxa</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>'''

    xg_s = stats_xg
    xg_extra = f'''<div class="xg-extra">
      <span>xG médio: <b>{xg_s["avg_xg"]}</b></span>
      <span>Golos médios: <b>{xg_s["avg_goals"]}</b></span>
      <span>Golos &gt; xG: <b>{xg_s["over_rate"]}%</b></span>
    </div>'''

    def league_table(rows, title):
        if not rows:
            return ""
        trs = "".join(f'''<tr>
          <td>{r["league"]}</td>
          <td>{r["picks"]}</td>
          <td style="color:{rate_color(r["rate"])};font-weight:700">{r["rate"]}%</td>
        </tr>''' for r in rows)
        return f'''<div class="league-block">
          <div class="lb-title">{title}</div>
          <table class="conf-table">
            <thead><tr><th>Liga</th><th>Picks</th><th>Taxa</th></tr></thead>
            <tbody>{trs}</tbody>
          </table>
        </div>'''

    return f'''<!DOCTYPE html>
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
.period{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 20px;margin-bottom:24px;display:flex;gap:24px;flex-wrap:wrap}}
.period span{{font-size:.8rem;color:var(--sub)}}
.period b{{color:var(--text)}}
.section-title{{font-size:1rem;font-weight:700;color:var(--text);margin:28px 0 14px;padding-left:4px;border-left:3px solid var(--blue)}}
.cards-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-bottom:28px}}
.stat-card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px}}
.sc-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}}
.sc-title{{font-size:1rem;font-weight:700}}
.sc-rate{{font-size:1.6rem;font-weight:800}}
.sc-sub{{font-size:.75rem;color:var(--muted);margin-bottom:14px}}
.xg-extra{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px}}
.xg-extra span{{font-size:.78rem;color:var(--sub)}}
.xg-extra b{{color:var(--text)}}
.conf-table{{width:100%;border-collapse:collapse;font-size:.78rem}}
.conf-table th{{text-align:left;color:var(--muted);padding:5px 6px;font-size:.68rem;text-transform:uppercase;letter-spacing:.4px;border-bottom:1px solid var(--border)}}
.conf-table td{{padding:6px 6px;border-bottom:1px solid #1a1f2e}}
.conf-table tr:last-child td{{border-bottom:none}}
.leagues-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}
.league-block{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px}}
.lb-title{{font-size:.82rem;font-weight:700;color:var(--sub);margin-bottom:12px;text-transform:uppercase;letter-spacing:.4px}}
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
  <div class="period">
    <span>📅 Período: <b>{date_min}</b> → <b>{date_max}</b></span>
    <span>🎯 Total de jogos analisados: <b>{total}</b></span>
  </div>

  <div class="section-title">Taxa de Acerto por Mercado</div>
  <div class="cards-grid">
    {stat_card(stats_1x2)}
    {stat_card(stats_o25)}
    {stat_card(stats_btts)}
    {stat_card({"label":"xG Alto","rate": round(stats_xg["over_rate"],1),"picks":stats_xg["picks"],"hits":0,"by_conf":{}}, xg_extra)}
  </div>

  <div class="section-title">Top 10 Ligas por Taxa de Acerto (mín. 10 picks)</div>
  <div class="leagues-grid">
    {league_table(top_1x2, "1X2")}
    {league_table(top_o25, "Over 2.5")}
    {league_table(top_btts, "BTTS")}
  </div>
</div>
<div class="footer">Matemática Da Bola · Backtest · BSD API</div>
</body>
</html>'''

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("[backtest] A buscar histórico completo...")
    preds = fetch_finished_predictions()
    print(f"[backtest] {len(preds)} jogos com resultado final encontrados")

    if not preds:
        print("[backtest] Sem dados históricos disponíveis")
        return

    records = [evaluate(p) for p in preds]

    stats_1x2 = calc_stats(records, "pick_1x2", "hit_1x2", "1X2")
    stats_o25 = calc_stats(records, "pick_o25",  "hit_o25",  "Over 2.5")
    stats_btts = calc_stats(records, "pick_btts", "hit_btts", "BTTS")
    stats_xg   = calc_xg_stats(records)
    top_1x2  = top_leagues(records, "pick_1x2", "hit_1x2")
    top_o25  = top_leagues(records, "pick_o25",  "hit_o25")
    top_btts = top_leagues(records, "pick_btts", "hit_btts")

    print(f"[backtest] 1X2:    {stats_1x2['picks']} picks, {stats_1x2['rate']}% acerto")
    print(f"[backtest] Over2.5:{stats_o25['picks']} picks, {stats_o25['rate']}% acerto")
    print(f"[backtest] BTTS:   {stats_btts['picks']} picks, {stats_btts['rate']}% acerto")
    print(f"[backtest] xG:     {stats_xg['picks']} jogos, avg xG {stats_xg['avg_xg']} vs {stats_xg['avg_goals']} golos reais")

    html = build_backtest_html(records, stats_1x2, stats_o25, stats_btts, stats_xg,
                                top_1x2, top_o25, top_btts)
    os.makedirs("docs", exist_ok=True)
    with open("docs/backtest.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("[backtest] docs/backtest.html gerado")

if __name__ == "__main__":
    main()
