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
        print(f"    [pred] event_id={event_id} → {data}")
        results = data.get("results", [])
        return results[0] if results else None
    except Exception as e:
        print(f"    [pred] event_id={event_id} → ERRO: {e}")
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
def has_pred_data(pred):
    """Só mostra card se tiver pelo menos probabilidade home_win."""
    if not pred:
        return False
    return pred.get("home_win") is not None

def prob_bar(pct_float, color):
    w = int(pct_float * 100)
    return f'<div style="height:4px;border-radius:2px;background:#23263a;margin-top:4px"><div style="width:{w}%;height:100%;background:{color};border-radius:2px"></div></div>'

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

    hw  = float(pred.get("home_win") or 0)
    dr  = float(pred.get("draw") or 0)
    aw  = float(pred.get("away_win") or 0)
    o25 = float(pred.get("over_2_5") or 0)
    bt  = float(pred.get("btts_yes") or 0)
    xg_h  = pred.get("home_xg") or "–"
    xg_a  = pred.get("away_xg") or "–"
    score = pred.get("most_likely_score") or "–"

    # Destaque do resultado mais provável
    best = max(hw, dr, aw)
    hw_bold = "font-weight:800;color:#fff" if hw == best else ""
    dr_bold = "font-weight:800;color:#fff" if dr == best else ""
    aw_bold = "font-weight:800;color:#fff" if aw == best else ""

    pred_html = f"""
    <div class="pred-grid">
      <div class="pred-item" style="flex:1.2">
        <span class="label">🏠 {home}</span>
        <span class="val" style="{hw_bold}">{fmt_pct(hw)}</span>
        {prob_bar(hw, '#4f8ef7')}
      </div>
      <div class="pred-item" style="flex:0.8">
        <span class="label">Empate</span>
        <span class="val" style="{dr_bold}">{fmt_pct(dr)}</span>
        {prob_bar(dr, '#888')}
      </div>
      <div class="pred-item" style="flex:1.2">
        <span class="label">✈️ {away}</span>
        <span class="val" style="{aw_bold}">{fmt_pct(aw)}</span>
        {prob_bar(aw, '#4f8ef7')}
      </div>
    </div>
    <div class="pred-grid" style="margin-top:8px">
      <div class="pred-item"><span class="label">Over 2.5</span><span class="val">{fmt_pct(o25)}</span></div>
      <div class="pred-item"><span class="label">BTTS</span><span class="val">{fmt_pct(bt)}</span></div>
      <div class="pred-item"><span class="label">xG</span><span class="val">{xg_h}–{xg_a}</span></div>
      <div class="pred-item"><span class="label">Score</span><span class="val">{score}</span></div>
    </div>"""

    if vals:
        rows = ""
        for v in vals:
            rows += f"""
            <div class="value-row">
              <span class="badge-val">⚡ VALUE</span>
              <span><b>{v["market"]} {v["side"]}</b></span>
              <span>ML: <b>{v["ml_prob"]*100:.0f}%</b></span>
              <span>Pinnacle: <b>{v["pin_odds"]:.2f}</b></span>
              <span class="edge">+{v["edge"]*100:.1f}% edge</span>
            </div>"""
        value_html = f'<div class="value-block" style="margin-top:12px">{rows}</div>'
    else:
        value_html = '<p class="no-value" style="margin-top:10px">Sem value detectado vs Pinnacle</p>'

    return f"""
    <div class="card">
      <div class="card-header">
        <span class="league-tag">{league}</span>
        <span class="ko">🕐 {ko}</span>
      </div>
      <div class="teams">{home} <span class="vs">vs</span> {away}</div>
      {pred_html}
      {value_html}
    </div>"""

def build_html(enriched_list):
    today = today_str()
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Filtrar só jogos com predição disponível
    with_data    = [e for e in enriched_list if has_pred_data(e["pred"])]
    no_data      = [e for e in enriched_list if not has_pred_data(e["pred"])]
    value_count  = sum(1 for e in with_data if detect_value(e["pred"], e["odds"]))

    cards = "\n".join(match_card_html(e) for e in with_data)

    # Jogos sem dados — lista simples
    if no_data:
        no_data_rows = "".join(
            f'<li>{e["match"].get("_league_name","")} · '
            f'{e["match"].get("home_team","?")} vs {e["match"].get("away_team","?")}</li>'
            for e in no_data
        )
        no_data_html = f"""
        <div class="no-data-section">
          <p class="no-data-title">⚠️ {len(no_data)} jogo(s) sem predição BSD</p>
          <ul class="no-data-list">{no_data_rows}</ul>
        </div>"""
    else:
        no_data_html = ""

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
  :root{{--bg:#0f1117;--card:#1a1d27;--border:#2a2d3a;--accent:#4f8ef7;--green:#4CAF50;--text:#e0e0e0;--muted:#777}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;padding:24px 20px;max-width:860px;margin:0 auto}}
  header{{margin-bottom:24px}}
  header h1{{color:var(--accent);font-size:1.6rem;font-weight:800;letter-spacing:-.3px}}
  header p{{color:var(--muted);font-size:.8rem;margin-top:4px}}
  .stats-bar{{display:flex;gap:12px;margin-bottom:28px;flex-wrap:wrap}}
  .stat{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 22px;min-width:120px}}
  .stat .n{{font-size:2rem;font-weight:800;color:var(--accent);line-height:1}}
  .stat .n.green{{color:var(--green)}}
  .stat .l{{font-size:.72rem;color:var(--muted);margin-top:5px;text-transform:uppercase;letter-spacing:.5px}}
  .card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:16px;transition:border-color .2s}}
  .card:hover{{border-color:#4f8ef755}}
  .card-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}}
  .league-tag{{font-size:.75rem;color:var(--accent);font-weight:700;letter-spacing:.3px}}
  .ko{{font-size:.75rem;color:var(--muted)}}
  .teams{{font-size:1.15rem;font-weight:700;margin-bottom:16px;letter-spacing:-.2px}}
  .vs{{color:var(--muted);font-weight:400;margin:0 8px;font-size:.95rem}}
  .pred-grid{{display:flex;gap:8px}}
  .pred-item{{background:#1e2130;border:1px solid var(--border);border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;align-items:center;flex:1}}
  .pred-item .label{{font-size:.68rem;color:var(--muted);margin-bottom:4px;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}}
  .pred-item .val{{font-size:1rem;font-weight:700;color:var(--text)}}
  .value-block{{display:flex;flex-direction:column;gap:7px}}
  .value-row{{display:flex;align-items:center;gap:10px;font-size:.82rem;background:#162016;border:1px solid #2a432a;border-radius:8px;padding:8px 12px;flex-wrap:wrap}}
  .badge-val{{background:var(--green);color:#000;font-size:.68rem;font-weight:800;padding:3px 7px;border-radius:5px;flex-shrink:0;letter-spacing:.3px}}
  .edge{{color:var(--green);font-weight:800;margin-left:auto}}
  .no-value{{font-size:.78rem;color:var(--muted);font-style:italic}}
  .no-data-section{{background:#1a1a1a;border:1px solid var(--border);border-radius:10px;padding:14px 18px;margin-bottom:20px}}
  .no-data-title{{font-size:.82rem;color:var(--muted);margin-bottom:8px}}
  .no-data-list{{list-style:none;font-size:.78rem;color:#555;line-height:1.8}}
  .footer{{text-align:center;font-size:.72rem;color:#444;margin-top:36px;padding-top:16px;border-top:1px solid var(--border)}}
  .empty{{text-align:center;padding:60px 20px;color:var(--muted)}}
</style>
</head>
<body>
<header>
  <h1>⚽ Football Dashboard</h1>
  <p>Gerado em {now} · BSD CatBoost ML · Value threshold: +3% vs Pinnacle</p>
</header>
<div class="stats-bar">
  <div class="stat"><div class="n">{len(with_data)}</div><div class="l">Jogos com dados</div></div>
  <div class="stat"><div class="n green">{value_count}</div><div class="l">Com value</div></div>
  <div class="stat"><div class="n" style="color:var(--muted);font-size:1.4rem">{len(enriched_list)}</div><div class="l">Total jogos</div></div>
</div>
{cards if with_data else '<div class="empty">Sem predições disponíveis hoje.</div>'}
{no_data_html}
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
