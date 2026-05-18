"""
Matemática Da Bola — BSD API
Gera HTML com TODOS os jogos do dia que tenham predicoes na BSD API
Envia resumo ao Telegram e guarda HTML em docs/dashboard.html
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
def fetch_all_predictions():
    """Busca todas as predicoes do dia de uma vez — pagina ate ao fim."""
    today = today_str()
    all_preds = []
    offset = 0
    limit = 50
    while True:
        try:
            data = get("/predictions/", {
                "date": today,
                "limit": limit,
                "offset": offset,
            })
            results = data.get("results", [])
            all_preds.extend(results)
            print(f"  [fetch] offset={offset} -> {len(results)} predicoes")
            if not data.get("next"):
                break
            offset += limit
        except Exception as e:
            print(f"  [WARN] predicoes offset={offset} falhou: {e}")
            break
    return all_preds

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

# ── HTML ─────────────────────────────────────────────────────────────────────
LEAGUE_FLAGS = {
    "Premier League": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "Championship": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "La Liga": "🇪🇸", "Segunda División": "🇪🇸",
    "Bundesliga": "🇩🇪", "DFB Pokal": "🇩🇪",
    "Serie A": "🇮🇹",
    "Ligue 1": "🇫🇷", "Coupe de France": "🇫🇷",
    "Champions League": "🏆", "Europa League": "🏆", "Conference League": "🏆",
    "Allsvenskan": "🇸🇪",
    "Eliteserien": "🇳🇴",
    "Veikkausliiga": "🇫🇮",
    "Eredivisie": "🇳🇱",
    "Pro League": "🇧🇪",
    "Brasileirão Serie A": "🇧🇷", "Brasileirão Serie B": "🇧🇷",
    "Copa do Brasil": "🇧🇷",
    "Copa Libertadores": "🌎", "Copa Sudamericana": "🌎",
    "MLS": "🇺🇸",
    "Saudi Pro League": "🇸🇦",
    "J1 League": "🇯🇵",
    "Chinese Super League": "🇨🇳",
    "Ekstraklasa": "🇵🇱",
    "Scottish Premiership": "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
    "Superliga": "🇷🇴",
    "Parva Liga": "🇧🇬",
    "Super League": "🇨🇭",
    "Stoiximan Super League": "🇬🇷",
    "Nigeria Premier Football League": "🇳🇬",
    "CAF Champions League": "🌍",
    "Coupe de Tunisie": "🇹🇳",
}

def league_flag(name):
    for k, v in LEAGUE_FLAGS.items():
        if k.lower() in name.lower():
            return v
    return "⚽"

def confidence_badge(conf):
    """Converte confidence do modelo (0-1) em badge visual."""
    if conf is None:
        return ("MÉDIA", "#f59e0b", "#2a1f00")
    c = float(conf)
    if c >= 0.65:
        return ("ALTA", "#22c55e", "#0a2010")
    elif c >= 0.45:
        return ("MÉDIA", "#f59e0b", "#2a1f00")
    else:
        return ("BAIXA", "#ef4444", "#2a0808")

def tip_label(hw, dr, aw, o25, conf):
    """Gera tip principal para o apostador recreativo."""
    best = max(hw, dr, aw)
    if best == hw and hw >= 0.55:
        tip = "Vitória Casa"
    elif best == aw and aw >= 0.55:
        tip = "Vitória Fora"
    elif best == dr and dr >= 0.35:
        tip = "Empate provável"
    elif o25 >= 0.65:
        tip = "Over 2.5 Golos"
    else:
        tip = "Resultado incerto"
    return tip

def has_pred_data(pred):
    if not pred:
        return False
    return pred.get("home_win") is not None

def match_card_html(enriched):
    m    = enriched["match"]
    pred = enriched["pred"]
    conf = enriched.get("confidence")

    home   = m.get("home_team", "?")
    away   = m.get("away_team", "?")
    league = m.get("_league_name", "")
    flag   = league_flag(league)
    ko_raw = m.get("event_date", "")
    try:
        ko_dt  = datetime.fromisoformat(ko_raw.replace("Z", ""))
        ko     = ko_dt.strftime("%H:%M")
        ko_hour = ko_dt.hour
    except Exception:
        ko = ko_raw
        ko_hour = 0

    hw  = float(pred.get("home_win") or 0)
    dr  = float(pred.get("draw") or 0)
    aw  = float(pred.get("away_win") or 0)
    o25 = float(pred.get("over_2_5") or 0)
    bt  = float(pred.get("btts_yes") or 0)
    xg_h  = pred.get("home_xg") or "–"
    xg_a  = pred.get("away_xg") or "–"
    score = pred.get("most_likely_score") or "–"

    conf_label, conf_color, conf_bg = confidence_badge(conf)
    tip = tip_label(hw, dr, aw, o25, conf)

    # Barras de probabilidade 1X2
    def bar(p, highlight):
        col = "#4f8ef7" if highlight else "#3a4060"
        return f'''<div style="height:6px;border-radius:3px;background:#1a1d35;margin-top:5px">
          <div style="width:{int(p*100)}%;height:100%;background:{col};border-radius:3px;transition:width .3s"></div></div>'''

    best = max(hw, dr, aw)

    return f'''
    <div class="card" data-league="{league}" data-hour="{ko_hour}" data-conf="{conf_label}" data-o25="{1 if o25>=0.60 else 0}" data-btts="{1 if bt>=0.60 else 0}" data-xg="{1 if (float(xg_h) if xg_h != chr(8211) else 0) + (float(xg_a) if xg_a != chr(8211) else 0) >= 2.5 else 0}">
      <div class="card-top">
        <span class="league-pill">{flag} {league}</span>
        <div style="display:flex;gap:8px;align-items:center">
          <span class="conf-badge" style="background:{conf_bg};color:{conf_color};border:1px solid {conf_color}40">{conf_label}</span>
          <span class="ko-time">🕐 {ko}</span>
        </div>
      </div>

      <div class="teams-row">
        <span class="team home-team">{home}</span>
        <span class="score-badge">{score}</span>
        <span class="team away-team">{away}</span>
      </div>

      <div class="tip-row">
        <span class="tip-label">💡 {tip}</span>
      </div>

      <div class="probs-row">
        <div class="prob-col {"winner" if hw == best else ""}">
          <div class="prob-name">Casa</div>
          <div class="prob-val">{int(hw*100)}%</div>
          {bar(hw, hw == best)}
        </div>
        <div class="prob-col {"winner" if dr == best else ""}">
          <div class="prob-name">Empate</div>
          <div class="prob-val">{int(dr*100)}%</div>
          {bar(dr, dr == best)}
        </div>
        <div class="prob-col {"winner" if aw == best else ""}">
          <div class="prob-name">Fora</div>
          <div class="prob-val">{int(aw*100)}%</div>
          {bar(aw, aw == best)}
        </div>
      </div>

      <div class="extra-row">
        <div class="extra-pill {"hot" if o25 >= 0.65 else ""}">⚽ Over 2.5<span>{int(o25*100)}%</span></div>
        <div class="extra-pill {"hot" if bt >= 0.60 else ""}">🔁 BTTS<span>{int(bt*100)}%</span></div>
        <div class="extra-pill">📊 xG<span>{xg_h}–{xg_a}</span></div>
      </div>
    </div>'''

def build_html(enriched_list):
    today = today_str()
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    with_data = [e for e in enriched_list if has_pred_data(e["pred"])]

    # Listas para filtros
    leagues  = sorted(set(e["match"].get("_league_name","") for e in with_data))
    hours    = sorted(set(
        datetime.fromisoformat(e["match"].get("event_date","").replace("Z","")).hour
        for e in with_data if e["match"].get("event_date")
    ))

    high_conf  = sum(1 for e in with_data if confidence_badge(e.get("confidence"))[0] == "ALTA")
    total      = len(with_data)

    league_opts = "".join(f'<option value="{l}">{league_flag(l)} {l}</option>' for l in leagues)
    hour_opts   = "".join(f'<option value="{h}">{h:02d}:00</option>' for h in hours)

    cards = "\n".join(match_card_html(e) for e in with_data)

    return f'''<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Matemática Da Bola — {today}</title>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-WE48R4KL96"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments)}}gtag("js",new Date());gtag("config","G-WE48R4KL96");</script>
<style>
:root{{
  --bg:#0b0e1a;--card:#131728;--border:#1e2235;
  --blue:#4f8ef7;--green:#22c55e;--yellow:#f59e0b;--red:#ef4444;
  --text:#e8eaf0;--muted:#5a6080;--sub:#8892b0;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:"Segoe UI",system-ui,sans-serif;min-height:100vh}}
.header{{background:linear-gradient(135deg,#0f1729 0%,#131e3a 100%);border-bottom:1px solid var(--border);padding:20px 24px 16px}}
.header h1{{font-size:1.5rem;font-weight:800;background:linear-gradient(90deg,#4f8ef7,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.header-meta{{font-size:.75rem;color:var(--muted);margin-top:4px}}
.stats-strip{{display:flex;gap:0;border-bottom:1px solid var(--border);background:#0f1220}}
.stat-item{{flex:1;padding:14px 20px;border-right:1px solid var(--border);text-align:center}}
.stat-item:last-child{{border-right:none}}
.stat-n{{font-size:1.6rem;font-weight:800;line-height:1}}
.stat-l{{font-size:.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-top:3px}}
.filters{{padding:16px 24px;background:#0d1020;border-bottom:1px solid var(--border);display:flex;gap:10px;flex-wrap:wrap;align-items:center}}
.filters label{{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}
.filter-select{{background:#1a1d2e;border:1px solid var(--border);color:var(--text);padding:6px 12px;border-radius:8px;font-size:.8rem;cursor:pointer;outline:none}}
.filter-select:focus{{border-color:var(--blue)}}
.filter-btn{{background:#1a1d2e;border:1px solid var(--border);color:var(--sub);padding:6px 14px;border-radius:8px;font-size:.78rem;cursor:pointer;transition:all .15s}}
.filter-btn.active,.filter-btn:hover{{background:var(--blue);border-color:var(--blue);color:#fff}}
.btn-reset{{background:transparent;border:1px solid #2a2d3a;color:var(--muted);padding:6px 12px;border-radius:8px;font-size:.75rem;cursor:pointer}}
.btn-reset:hover{{border-color:var(--red);color:var(--red)}}
.cards-container{{padding:20px 24px;max-width:960px;margin:0 auto}}
.no-results{{text-align:center;padding:60px 20px;color:var(--muted);font-size:.9rem}}
/* CARD */
.card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px 18px;margin-bottom:14px;transition:border-color .2s,transform .15s;cursor:default}}
.card:hover{{border-color:#4f8ef740;transform:translateY(-1px)}}
.card.hidden{{display:none}}
.card-top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}}
.league-pill{{font-size:.72rem;color:var(--sub);font-weight:600;background:#1a1d2e;padding:3px 10px;border-radius:20px;border:1px solid var(--border)}}
.conf-badge{{font-size:.68rem;font-weight:800;padding:3px 9px;border-radius:20px;letter-spacing:.4px}}
.ko-time{{font-size:.72rem;color:var(--muted)}}
.teams-row{{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;gap:8px}}
.team{{font-size:1rem;font-weight:700;flex:1}}
.home-team{{text-align:left}}
.away-team{{text-align:right}}
.score-badge{{background:#1a1d35;border:1px solid var(--border);border-radius:8px;padding:5px 14px;font-size:1.1rem;font-weight:800;color:var(--blue);white-space:nowrap;min-width:54px;text-align:center}}
.tip-row{{margin-bottom:14px}}
.tip-label{{font-size:.78rem;color:var(--yellow);font-weight:600;background:#1f1800;border:1px solid #3a2f0020;padding:4px 12px;border-radius:6px;display:inline-block}}
.probs-row{{display:flex;gap:8px;margin-bottom:12px}}
.prob-col{{flex:1;background:#0f1220;border:1px solid var(--border);border-radius:10px;padding:10px;text-align:center;transition:border-color .2s}}
.prob-col.winner{{border-color:var(--blue);background:#0d1428}}
.prob-name{{font-size:.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:4px}}
.prob-val{{font-size:1.2rem;font-weight:800}}
.prob-col.winner .prob-val{{color:var(--blue)}}
.extra-row{{display:flex;gap:8px;flex-wrap:wrap}}
.extra-pill{{display:flex;align-items:center;gap:6px;background:#0f1220;border:1px solid var(--border);border-radius:8px;padding:6px 12px;font-size:.75rem;color:var(--sub)}}
.extra-pill span{{font-weight:700;color:var(--text)}}
.extra-pill.hot{{border-color:#22c55e40;background:#0a1a0e;color:var(--green)}}
.extra-pill.hot span{{color:var(--green)}}
.footer{{text-align:center;padding:30px;font-size:.7rem;color:#333;border-top:1px solid var(--border)}}
@media(max-width:600px){{
  .header,.filters,.cards-container{{padding-left:14px;padding-right:14px}}
  .team{{font-size:.88rem}}
  .filters{{gap:6px}}
}}
</style>
</head>
<body>
<div class="header">
  <h1>⚽ Matemática Da Bola</h1>
  <div class="header-meta">Actualizado em {now}</div>
</div>
<div class="stats-strip">
  <div class="stat-item"><div class="stat-n" style="color:var(--blue)">{total}</div><div class="stat-l">Jogos hoje</div></div>
  <div class="stat-item"><div class="stat-n" style="color:var(--green)">{high_conf}</div><div class="stat-l">Alta confiança</div></div>
  <div class="stat-item"><div class="stat-n" style="color:var(--yellow)">{sum(1 for e in with_data if confidence_badge(e.get("confidence"))[0]=="MÉDIA")}</div><div class="stat-l">Média confiança</div></div>
</div>
<div class="filters">
  <label>Liga</label>
  <select class="filter-select" id="f-league" onchange="applyFilters()">
    <option value="">Todas</option>
    {league_opts}
  </select>
  <label>Hora</label>
  <select class="filter-select" id="f-hour" onchange="applyFilters()">
    <option value="">Qualquer hora</option>
    {hour_opts}
  </select>
  <label>Confiança</label>
  <button class="filter-btn" id="btn-alta" onclick="toggleConf('ALTA')">🟢 Alta</button>
  <button class="filter-btn" id="btn-media" onclick="toggleConf('MÉDIA')">🟡 Média</button>
  <button class="filter-btn" id="btn-baixa" onclick="toggleConf('BAIXA')">🔴 Baixa</button>
  <label>Mercado</label>
  <button class="filter-btn" id="btn-1x2" onclick="toggleMarket('1x2')">1X2</button>
  <button class="filter-btn" id="btn-o25" onclick="toggleMarket('o25')">Over 2.5</button>
  <button class="filter-btn" id="btn-btts" onclick="toggleMarket('btts')">BTTS</button>
  <button class="filter-btn" id="btn-xg" onclick="toggleMarket('xg')">xG Alto</button>
  <button class="btn-reset" onclick="resetFilters()">✕ Limpar</button>
</div>
<div class="cards-container" id="cards">
{cards}
<div class="no-results hidden" id="no-results">Nenhum jogo corresponde aos filtros seleccionados.</div>
</div>
<div class="footer">Matemática Da Bola · BSD API · {today}</div>
<script>
let activeConf = null;
let activeMarket = null;
function applyFilters() {{
  const league = document.getElementById("f-league").value;
  const hour   = document.getElementById("f-hour").value;
  let visible  = 0;
  document.querySelectorAll(".card").forEach(c => {{
    const okL = !league || c.dataset.league === league;
    const okH = !hour   || c.dataset.hour === hour;
    const okC = !activeConf || c.dataset.conf === activeConf;
    const okM = !activeMarket || (
      activeMarket === "1x2" ? true :
      activeMarket === "o25"  ? c.dataset.o25 === "1" :
      activeMarket === "btts" ? c.dataset.btts === "1" :
      activeMarket === "xg"   ? c.dataset.xg === "1" : true
    );
    const show = okL && okH && okC && okM;
    c.classList.toggle("hidden", !show);
    if(show) visible++;
  }});
  document.getElementById("no-results").classList.toggle("hidden", visible > 0);
}}
function toggleConf(val) {{
  activeConf = activeConf === val ? null : val;
  ["alta","media","baixa"].forEach(b => document.getElementById("btn-"+b).classList.remove("active"));
  if(activeConf) {{
    const map = {{"ALTA":"alta","MÉDIA":"media","BAIXA":"baixa"}};
    document.getElementById("btn-"+map[activeConf]).classList.add("active");
  }}
  applyFilters();
}}
function toggleMarket(val) {{
  activeMarket = activeMarket === val ? null : val;
  ["1x2","o25","btts","xg"].forEach(b => document.getElementById("btn-"+b).classList.remove("active"));
  if(activeMarket) document.getElementById("btn-"+activeMarket).classList.add("active");
  applyFilters();
}}
function resetFilters() {{
  document.getElementById("f-league").value = "";
  document.getElementById("f-hour").value   = "";
  activeConf = null; activeMarket = null;
  ["alta","media","baixa","1x2","o25","btts","xg"].forEach(b => document.getElementById("btn-"+b).classList.remove("active"));
  applyFilters();
}}
</script>
</body>
</html>'''

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram(enriched_list):
    today = today_str()
    with_value = [(e, detect_value(e["pred"], e["odds"])) for e in enriched_list]
    with_value = [(e, v) for e, v in with_value if v]

    lines = [f"⚽ *Matemática Da Bola — {today}*"]
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
    today = today_str()
    print(f"[dashboard] {today} — a buscar predicoes...")

    # 1. Buscar todas as predicoes do dia de uma vez (paginadas)
    all_preds = fetch_all_predictions()
    print(f"[dashboard] {len(all_preds)} predicoes encontradas")

    # 2. Para cada predicao, buscar odds e montar enriched
    enriched_list = []
    seen = set()
    for pred in all_preds:
        event = pred.get("event", {})
        eid = event.get("id")
        if eid in seen:
            continue
        seen.add(eid)

        # Construir estrutura de match compativel com o resto do codigo
        m = {
            "id":           eid,
            "home_team":    event.get("home_team"),
            "away_team":    event.get("away_team"),
            "event_date":   event.get("event_date"),
            "_league_name": event.get("league_name", "?"),
        }

        # Normalizar pred para o formato esperado pelo match_card_html
        markets = pred.get("markets", {})
        mr  = markets.get("match_result", {})
        xg  = markets.get("expected_goals", {})
        ou  = markets.get("over_under", {})
        bt  = markets.get("btts", {})
        sc  = markets.get("score", {})

        pred_norm = {
            "home_win":  mr.get("prob_home", 0) / 100 if mr.get("prob_home") else None,
            "draw":      mr.get("prob_draw", 0) / 100 if mr.get("prob_draw") else None,
            "away_win":  mr.get("prob_away", 0) / 100 if mr.get("prob_away") else None,
            "over_2_5":  ou.get("prob_over_25", 0) / 100 if ou.get("prob_over_25") else None,
            "btts_yes":  bt.get("prob_yes", 0) / 100 if bt.get("prob_yes") else None,
            "home_xg":   round(xg.get("home", 0), 2) if xg.get("home") else None,
            "away_xg":   round(xg.get("away", 0), 2) if xg.get("away") else None,
            "most_likely_score": sc.get("most_likely"),
        }

        odds = fetch_odds(eid)
        home = m.get("home_team", "?")
        away = m.get("away_team", "?")
        league = m.get("_league_name", "")
        print(f"  -> {home} vs {away} [{league}]")
        conf = pred.get("model", {}).get("confidence")
        enriched_list.append({"match": m, "pred": pred_norm, "odds": odds, "confidence": conf})

    # 3. Ordenar por hora de kickoff
    enriched_list.sort(key=lambda e: e["match"].get("event_date", ""))

    print(f"[dashboard] {len(enriched_list)} jogos com predicao")

    html = build_html(enriched_list)
    os.makedirs("docs", exist_ok=True)
    with open("docs/dashboard.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("[dashboard] docs/dashboard.html guardado")

    send_telegram(enriched_list)
    print("[dashboard] Telegram enviado ✓")

if __name__ == "__main__":
    main()
