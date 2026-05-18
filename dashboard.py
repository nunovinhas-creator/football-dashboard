"""
Football Dashboard — BSD API
Gera HTML com jogos do dia + predições ML + value vs Pinnacle
Envia resumo ao Telegram e guarda HTML em docs/dashboard.html
Ligas: EPL, La Liga, UCL, UEL, Allsvenskan, Eliteserien, Veikkausliiga,
       Brasileirão A/B, Copa do Brasil, Libertadores, Sudamericana
"""

import os
import requests
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
BSD_KEY  = os.environ["BSD_API_KEY"]
TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT  = os.environ["TG_CHAT_ID"]

BASE    = "https://sports.bzzoiro.com/api/v2"
HEADERS = {"Authorization": f"Token {BSD_KEY}"}

LEAGUES = {
    # Europa
    1:  "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League",
    3:  "🇪🇸 La Liga",
    7:  "🏆 Champions League",
    8:  "🏆 Europa League",
    # Nórdicos (Abr–Nov)
    26: "🇸🇪 Allsvenskan",
    54: "🇳🇴 Eliteserien",
    55: "🇫🇮 Veikkausliiga",
    # América do Sul
    9:  "🇧🇷 Brasileirão Serie A",
    34: "🇧🇷 Brasileirão Serie B",
    35: "🇧🇷 Copa do Brasil",
    32: "🌎 Copa Libertadores",
    33: "🌎 Copa Sudamericana",
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def get(path, params=None):
    r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def fmt_pct(v):
    if v is None:
        return "–"
    return f"{round(float(v) * 100)}%"

# ── Fetch ─────────────────────────────────────────────────────────────────────
def fetch_matches():
    today = today_str()
    matches = []
    for league_id, league_name in LEAGUES.items():
        try:
            data = get("/events/", {
                "league_id": league_id,
                "date_from": today,
                "date_to":   today,
                "limit":     20,
            })
            for m in data.get("results", []):
                m["_league_name"] = league_name
                matches.append(m)
        except Exception as e:
            print(f"  [WARN] Liga {league_id} falhou: {e}")
    return matches

def fetch_prediction(event_id):
    try:
        data = get("/predictions/", {"event_id": event_id})
        results = data.get("results", [])
        return results[0] if results else None
    except Exception:
        return None

def fetch_odds(event_id):
    try:
        return get(f"/events/{event_id}/odds/comparison/")
    except Exception:
        return None

def enrich(match):
    eid  = match["id"]
    pred = fetch_prediction(eid)
    odds = fetch_odds(eid)
    return {"match": match, "pred": pred, "odds": odds}

# ── Value detection ───────────────────────────────────────────────────────────
def detect_value(pred, odds):
    if not pred or not odds:
        return []
    pin = None
    for b in odds.get("bookmakers", []):
        if "pinnacle" in b.get("bookmaker_name", "").lower() or \
           b.get("bookmaker_slug") == "pinnacle":
            pin = b
            break
    if not pin:
        return []

    mappings = [
        ("1X2",     "HOME",  pred.get("home_win"), pin.get("home_odds")),
        ("1X2",     "DRAW",  pred.get("draw"),      pin.get("draw_odds")),
        ("1X2",     "AWAY",  pred.get("away_win"),  pin.get("away_odds")),
        ("Over2.5", "OVER",  pred.get("over_2_5"),  pin.get("over_2_5")),
        ("BTTS",    "YES",   pred.get("btts_yes"),  pin.get("btts_yes")),
    ]
    values = []
    for market, side, ml_prob, pin_odds in mappings:
        if ml_prob is None or pin_odds is None:
            continue
        try:
            ml_p  = float(ml_prob)
            pin_p = 1 / float(pin_odds)
            edge  = ml_p - pin_p
            if edge > 0.03:
                values.append({
                    "market":   market,
                    "side":     side,
                    "ml_prob":  ml_p,
                    "pin_odds": float(pin_odds),
                    "edge":     edge,
                })
        except (TypeError, ZeroDivisionError, ValueError):
            continue
    return values

# ── HTML ──────────────────────────────────────────────────────────────────────
def match_card_html(enriched):
    m    = enriched["match"]
    pred = enriched["pred"]
    odds = enriched["odds"]
    vals = detect_value(pred, odds)

    home   = m.get("home_team", "?")
    away   = m.get("away_team", "?")
    league = m.get("_league_name", "")
    ko_raw = m.get("event_date", "")
    try:
        ko = datetime.fromisoformat(ko_raw.replace("Z", "")).strftime("%H:%M UTC")
    except Exception:
        ko = ko_raw

    if pred:
        xg_h  = pred.get("home_xg") or "–"
        xg_a  = pred.get("away_xg") or "–"
        score = pred.get("most_likely_score") or "–"
        pred_html = f"""
        <div class="pred-grid">
          <div class="pred-item"><span class="label">1</span><span class="val">{fmt_pct(pred.get("home_win"))}</span></div>
          <div class="pred-item"><span class="label">X</span><span class="val">{fmt_pct(pred.get("draw"))}</span></div>
          <div class="pred-item"><span class="label">2</span><span class="val">{fmt_pct(pred.get("away_win"))}</span></div>
          <div class="pred-item"><span class="label">O2.5</span><span class="val">{fmt_pct(pred.get("over_2_5"))}</span></div>
          <div class="pred-item"><span class="label">BTTS</span><span class="val">{fmt_pct(pred.get("btts_yes"))}</span></div>
          <div class="pred-item"><span class="label">xG</span><span class="val">{xg_h}–{xg_a}</span></div>
          <div class="pred-item"><span class="label">Score</span><span class="val">{score}</span></div>
        </div>"""
    else:
        pred_html = '<p class="no-data">Sem predição disponível</p>'

    if vals:
        rows = ""
        for v in vals:
            rows += f"""
            <div class="value-row">
              <span class="badge-val">VALUE</span>
              <span>{v["market"]} {v["side"]}</span>
              <span>ML: {v["ml_prob"]*100:.0f}%</span>
              <span>Odds: {v["pin_odds"]:.2f}</span>
              <span class="edge">+{v["edge"]*100:.1f}%</span>
            </div>"""
        value_html = f'<div class="value-block">{rows}</div>'
    else:
        value_html = '<p class="no-value">Sem value vs Pinnacle</p>'

    return f"""
    <div class="card">
      <div class="card-header">
        <span class="league-tag">{league}</span>
        <span class="ko">{ko}</span>
      </div>
      <div class="teams">{home} <span class="vs">vs</span> {away}</div>
      {pred_html}
      <div class="divider"></div>
      {value_html}
    </div>"""

def build_html(enriched_list):
    today       = today_str()
    now         = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total       = len(enriched_list)
    value_count = sum(1 for e in enriched_list if detect_value(e["pred"], e["odds"]))
    cards       = "\n".join(match_card_html(e) for e in enriched_list)

    return f"""<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Football Dashboard — {today}</title>
<!-- Google Analytics -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-WE48R4KL96"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', 'G-WE48R4KL96');
</script>
<style>
  :root {{
    --bg:#0f1117; --card:#1a1d27; --border:#2a2d3a;
    --accent:#4f8ef7; --green:#4CAF50; --text:#e0e0e0; --muted:#888;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:'Segoe UI',sans-serif;padding:20px;max-width:900px;margin:0 auto}}
  h1{{color:var(--accent);font-size:1.5rem;margin-bottom:4px}}
  .sub{{color:var(--muted);font-size:.82rem;margin-bottom:22px}}
  .stats-bar{{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}}
  .stat{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px 20px}}
  .stat .n{{font-size:1.7rem;font-weight:700;color:var(--accent)}}
  .stat .n.green{{color:var(--green)}}
  .stat .l{{font-size:.72rem;color:var(--muted);margin-top:2px}}
  .card{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:14px}}
  .card-header{{display:flex;justify-content:space-between;margin-bottom:8px}}
  .league-tag{{font-size:.78rem;color:var(--accent);font-weight:600}}
  .ko{{font-size:.78rem;color:var(--muted)}}
  .teams{{font-size:1.08rem;font-weight:700;margin-bottom:14px}}
  .vs{{color:var(--muted);font-weight:400;margin:0 8px}}
  .pred-grid{{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:10px}}
  .pred-item{{background:#23263a;border-radius:6px;padding:6px 10px;display:flex;flex-direction:column;align-items:center;min-width:54px}}
  .pred-item .label{{font-size:.68rem;color:var(--muted)}}
  .pred-item .val{{font-size:.93rem;font-weight:600}}
  .divider{{height:1px;background:var(--border);margin:10px 0}}
  .value-block{{display:flex;flex-direction:column;gap:6px}}
  .value-row{{display:flex;align-items:center;gap:10px;font-size:.81rem;background:#1e2a1e;border:1px solid #2d4a2d;border-radius:6px;padding:6px 10px;flex-wrap:wrap}}
  .badge-val{{background:var(--green);color:#000;font-size:.67rem;font-weight:700;padding:2px 6px;border-radius:4px;flex-shrink:0}}
  .edge{{color:var(--green);font-weight:700;margin-left:auto}}
  .no-data,.no-value{{font-size:.79rem;color:var(--muted);font-style:italic}}
  .footer{{text-align:center;font-size:.73rem;color:var(--muted);margin-top:30px;padding-top:16px;border-top:1px solid var(--border)}}
</style>
</head>
<body>
<h1>⚽ Football Dashboard</h1>
<p class="sub">Gerado em {now} · BSD CatBoost ML · Value threshold: +3% vs Pinnacle</p>
<div class="stats-bar">
  <div class="stat"><div class="n">{total}</div><div class="l">Jogos hoje</div></div>
  <div class="stat"><div class="n green">{value_count}</div><div class="l">Com value detectado</div></div>
</div>
{cards if cards else '<p style="color:var(--muted);text-align:center;padding:40px">Sem jogos hoje nas ligas cobertas.</p>'}
<p class="footer">Football Dashboard · BSD API · nunovinhas-creator/football-dashboard</p>
</body>
</html>"""

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(enriched_list):
    today = today_str()
    with_value = [(e, detect_value(e["pred"], e["odds"])) for e in enriched_list]
    with_value = [(e, v) for e, v in with_value if v]

    lines = [f"⚽ *Football Dashboard — {today}*"]
    lines.append(f"📋 {len(enriched_list)} jogos · ✅ {len(with_value)} com value\n")

    for e, vals in with_value:
        m = e["match"]
        lines.append(f"{m.get('_league_name','')}")
        lines.append(f"*{m.get('home_team','?')} vs {m.get('away_team','?')}*")
        for v in vals:
            lines.append(
                f"  ✅ {v['market']} {v['side']} | "
                f"ML {v['ml_prob']*100:.0f}% | "
                f"Odds {v['pin_odds']:.2f} | "
                f"edge +{v['edge']*100:.1f}%"
            )
        lines.append("")

    if not with_value:
        lines.append("_Sem value detectado hoje._")

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(
        url,
        json={"chat_id": TG_CHAT, "text": "\n".join(lines), "parse_mode": "Markdown"},
        timeout=10,
    )

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"[dashboard] {today_str()} — a buscar jogos...")
    matches = fetch_matches()
    print(f"[dashboard] {len(matches)} jogos encontrados")

    enriched_list = []
    for m in matches:
        print(f"  → {m.get('home_team')} vs {m.get('away_team')} [{m.get('_league_name')}]")
        enriched_list.append(enrich(m))

    html = build_html(enriched_list)
    os.makedirs("docs", exist_ok=True)
    with open("docs/dashboard.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("[dashboard] docs/dashboard.html guardado")

    send_telegram(enriched_list)
    print("[dashboard] Telegram enviado ✓")

if __name__ == "__main__":
    main()
