"""
Matemática Da Bola — BSD API v2 (fixed)
"""

import os
import json
import requests
from datetime import datetime, timezone, timedelta

BSD_KEY  = os.environ["BSD_API_KEY"]
TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT  = os.environ["TG_CHAT_ID"]

TREBLES_FILE = "docs/trebles.json"

BASE    = "https://sports.bzzoiro.com/api/v2"
HEADERS = {"Authorization": f"Token {BSD_KEY}"}

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

# FIX BUG 4: parse seguro de datas ISO com ou sem Z/offset
def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def fetch_all_predictions():
    all_preds = []
    offset = 0
    limit = 50
    while True:
        try:
            data = get("/predictions/", {"limit": limit, "offset": offset})
            results = data.get("results", [])
            if not results:
                break
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

# FIX BUG 1: fetch_prediction estava em falta
def fetch_prediction(event_id):
    try:
        data = get("/predictions/", {"event_id": event_id, "limit": 1})
        results = data.get("results", [])
        return results[0] if results else None
    except Exception:
        return None

def enrich(match):
    eid  = match["id"]
    pred = fetch_prediction(eid)
    odds = fetch_odds(eid)
    return {"match": match, "pred": pred, "odds": odds}

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
                values.append({"market": market, "side": side,
                                "ml_prob": ml_p, "pin_odds": float(pin_odds), "edge": edge})
        except (TypeError, ZeroDivisionError, ValueError):
            continue
    return values

LEAGUE_FLAGS = {
    "Premier League": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "Championship": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "La Liga": "🇪🇸", "Segunda División": "🇪🇸",
    "Bundesliga": "🇩🇪", "Serie A": "🇮🇹",
    "Ligue 1": "🇫🇷", "Champions League": "🏆",
    "Europa League": "🏆", "Conference League": "🏆",
    "Allsvenskan": "🇸🇪", "Eliteserien": "🇳🇴", "Veikkausliiga": "🇫🇮",
    "Eredivisie": "🇳🇱", "Pro League": "🇧🇪",
    "Brasileirão Serie A": "🇧🇷", "Brasileirão Serie B": "🇧🇷",
    "Copa do Brasil": "🇧🇷", "Copa Libertadores": "🌎", "Copa Sudamericana": "🌎",
    "MLS": "🇺🇸", "Saudi Pro League": "🇸🇦", "J1 League": "🇯🇵",
    "Chinese Super League": "🇨🇳", "Ekstraklasa": "🇵🇱",
    "Scottish Premiership": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "Superliga": "🇷🇴",
    "Parva Liga": "🇧🇬", "Super League": "🇨🇭",
    "Stoiximan Super League": "🇬🇷",
}

def league_flag(name):
    for k, v in LEAGUE_FLAGS.items():
        if k.lower() in name.lower():
            return v
    return "⚽"

def confidence_badge(conf):
    if conf is None:
        return ("MÉDIA", "#f59e0b", "#2a1f00")
    c = float(conf)
    if c >= 0.65:   return ("ALTA",  "#22c55e", "#0a2010")
    elif c >= 0.45: return ("MÉDIA", "#f59e0b", "#2a1f00")
    else:           return ("BAIXA", "#ef4444", "#2a0808")

def tip_label(hw, dr, aw, o25, conf):
    best = max(hw, dr, aw)
    if best == hw and hw >= 0.55:   return "Vitória Casa"
    elif best == aw and aw >= 0.55: return "Vitória Fora"
    elif best == dr and dr >= 0.35: return "Empate provável"
    elif o25 >= 0.65:               return "Over 2.5 Golos"
    else:                           return "Resultado incerto"

def has_pred_data(pred):
    return bool(pred and pred.get("home_win") is not None)

def match_card_html(enriched):
    m      = enriched["match"]
    pred   = enriched["pred"]
    conf   = enriched.get("confidence")
    result = enriched.get("result")

    home   = m.get("home_team", "?")
    away   = m.get("away_team", "?")
    league = m.get("_league_name", "")
    flag   = league_flag(league)
    ko_raw = m.get("event_date", "")

    ko_dt = parse_dt(ko_raw)
    if ko_dt:
        ko      = ko_dt.strftime("%d/%m %H:%M")
        ko_date = ko_dt.strftime("%Y-%m-%d")
        ko_hour = ko_dt.hour
    else:
        ko = ko_raw; ko_date = ""; ko_hour = 0

    hw  = float(pred.get("home_win") or 0)
    dr  = float(pred.get("draw")     or 0)
    aw  = float(pred.get("away_win") or 0)
    o25 = float(pred.get("over_2_5") or 0)
    bt  = float(pred.get("btts_yes") or 0)
    xg_h = pred.get("home_xg") or "–"
    xg_a = pred.get("away_xg") or "–"
    score_pred = pred.get("most_likely_score") or "–"
    try:    xg_total = round(float(xg_h) + float(xg_a), 2)
    except: xg_total = 0

    conf_label, _, _ = confidence_badge(conf)
    tip  = tip_label(hw, dr, aw, o25, conf)
    best = max(hw, dr, aw)

    status = m.get("status", "notstarted")
    # FIX BUG 3: is_finished agora correcto
    is_finished = (status == "finished")
    is_live     = status in ("inprogress", "live", "halftime")
    card_class  = "card finished" if is_finished else ("card live-now" if is_live else "card")

    if (is_finished or is_live) and result:
        color = "var(--yellow)" if is_live else "var(--green)"
        border_color = "var(--yellow)" if is_live else "var(--win-border)"
        label = "⏱ A decorrer" if is_live else "Final"
        score_html = f'''<div class="score-area">
          <div class="final-score" style="color:{color};border-color:{border_color}">{result["home"]} – {result["away"]}</div>
          <div class="score-label" style="color:{color}">{label}</div>
        </div>'''
    else:
        score_html = f'''<div class="score-area">
          <div class="predicted-score">{score_pred}</div>
          <div class="score-label">Previsão</div>
        </div>'''

    if conf_label == "ALTA":
        badge_style = "background:#0d2818;color:#4ade80;border:1px solid #166534"
    elif conf_label == "MÉDIA":
        badge_style = "background:#2a1f00;color:#fbbf24;border:1px solid #78350f"
    else:
        badge_style = "background:#2a0a0a;color:#f87171;border:1px solid #7f1d1d"

    def bar(p, highlight):
        color = "#60a5fa" if highlight else "#2d3748"
        return f'<div class="prob-bar"><div class="prob-bar-fill" style="width:{int(p*100)}%;background:{color}"></div></div>'

    o25_class = "extra-pill hot-green" if o25 >= 0.61 else "extra-pill"
    bt_class  = "extra-pill hot-green" if bt  >= 0.61 else "extra-pill"
    xg_class  = "extra-pill hot-blue"  if xg_total >= 2.5 else "extra-pill"

    ko_display = "🔴 LIVE" if is_live else ("✅ Final" if is_finished else f"🕐 {ko}")

    return f'''
    <div class="{card_class}" data-league="{league}" data-date="{ko_date}" data-hour="{ko_hour}" data-conf="{conf_label}" data-hw="{int(hw*100)}" data-dr="{int(dr*100)}" data-aw="{int(aw*100)}" data-o25="{int(o25*100)}" data-btts="{int(bt*100)}" data-xgtotal="{xg_total}">
      <div class="card-top">
        <div class="league-pill">{flag} {league}</div>
        <div class="card-right">
          <span class="conf-badge" style="{badge_style}">{conf_label}</span>
          <span class="ko-time">{ko_display}</span>
        </div>
      </div>
      <div class="card-body">
        <div class="teams-row">
          <span class="team home-team">{home}</span>
          {score_html}
          <span class="team away-team">{away}</span>
        </div>
        <div class="tip-row"><span class="tip-badge">💡 {tip}</span></div>
        <div class="probs-row">
          <div class="prob-col {"winner" if hw==best else ""}">
            <div class="prob-name">Casa</div>
            <div class="prob-val">{int(hw*100)}%</div>
            {bar(hw, hw==best)}
          </div>
          <div class="prob-col {"winner" if dr==best else ""}">
            <div class="prob-name">Empate</div>
            <div class="prob-val">{int(dr*100)}%</div>
            {bar(dr, dr==best)}
          </div>
          <div class="prob-col {"winner" if aw==best else ""}">
            <div class="prob-name">Fora</div>
            <div class="prob-val">{int(aw*100)}%</div>
            {bar(aw, aw==best)}
          </div>
        </div>
        <div class="extra-row">
          <div class="{o25_class}">⚽ Over 2.5 <span>{int(o25*100)}%</span></div>
          <div class="{bt_class}">🔁 BTTS <span>{int(bt*100)}%</span></div>
          <div class="{xg_class}">📊 xG <span>{xg_h}–{xg_a}</span></div>
        </div>
      </div>
    </div>'''

def load_todays_treble():
    try:
        if os.path.exists(TREBLES_FILE):
            with open(TREBLES_FILE, encoding="utf-8") as f:
                data = json.load(f)
            today = today_str()
            for t in data.get("pending", []):
                if t.get("date") == today:
                    return t
    except Exception:
        pass
    return None

def treble_banner_html(treble):
    if not treble:
        return ""
    mkt_label = {"BTTS": "🔁 BTTS", "1X2-H": "🏠 Casa", "1X2-D": "🤝 Empate", "1X2-A": "✈️ Fora"}
    conf_col  = {"ALTA": "#4ade80", "MÉDIA": "#fbbf24", "BAIXA": "#f87171"}
    picks_html = ""
    for pk in treble["picks"]:
        col  = conf_col.get(pk.get("conf",""), "#94a3b8")
        mkt  = mkt_label.get(pk["market"], pk["market"])
        odds = f"@{pk['odds']:.2f}" if pk.get("odds") else ""
        picks_html += (
            f'<div class="tb-pick">'
            f'<span class="tb-pick-league">{pk["league"]}</span>'
            f'<span class="tb-pick-teams">{pk["home"]} vs {pk["away"]}</span>'
            f'<span class="tb-pick-mkt">{mkt}</span>'
            f'<span style="color:{col};font-weight:700">{int(pk["prob"]*100)}%</span>'
            f'<span class="tb-pick-odds">{odds}</span>'
            f'</div>'
        )
    combined = f"{treble['combined_odds']:.2f}" if treble.get("combined_odds") else "–"
    return (
        f'<div class="treble-banner">'
        f'<div class="treble-banner-hdr">'
        f'<span>🎯 Tripla do Dia</span>'
        f'<span class="treble-banner-odds">Odds combinadas: <b>{combined}</b>'
        f' · <a href="backtest.html" class="treble-link">Ver histórico →</a></span>'
        f'</div>'
        f'{picks_html}'
        f'</div>'
    )

def build_html(enriched_list):
    today = today_str()
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    with_data = [e for e in enriched_list if has_pred_data(e["pred"])]
    todays_treble = load_todays_treble()
    banner_html   = treble_banner_html(todays_treble)

    leagues  = sorted(set(e["match"].get("_league_name","") for e in with_data))
    dates    = sorted(set(
        e["match"].get("event_date","")[:10]
        for e in with_data if e["match"].get("event_date")
    ))

    high_conf = sum(1 for e in with_data if confidence_badge(e.get("confidence"))[0] == "ALTA")
    total     = len(with_data)

    league_opts = "".join(f'<option value="{l}">{league_flag(l)} {l}</option>' for l in leagues)
    date_opts   = "".join(f'<option value="{d}">{d}</option>' for d in dates)

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
  --bg:#0d1117;
  --surface:#161b27;
  --card:#1c2333;
  --card-hover:#1f2740;
  --border:#2d3748;
  --border-light:#3a4560;
  --blue:#60a5fa;
  --blue-dim:#3b82f6;
  --green:#4ade80;
  --green-dim:#22c55e;
  --yellow:#fbbf24;
  --red:#f87171;
  --purple:#a78bfa;
  --text:#f1f5f9;
  --sub:#94a3b8;
  --muted:#4a5568;
  --win-bg:#0d2818;
  --win-border:#166534;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:"Inter","Segoe UI",system-ui,sans-serif;min-height:100vh}}

/* HEADER */
.header{{
  background:linear-gradient(180deg,#0a0f1e 0%,#0d1117 100%);
  border-bottom:1px solid var(--border);
  padding:22px 28px 18px;
  display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:10px
}}
.header-left h1{{
  font-size:1.6rem;font-weight:800;letter-spacing:-.5px;
  background:linear-gradient(90deg,#60a5fa 0%,#a78bfa 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent
}}
.header-left .meta{{font-size:.72rem;color:var(--muted);margin-top:5px}}
.live-dot{{
  display:inline-block;width:7px;height:7px;border-radius:50%;
  background:#4ade80;margin-right:5px;
  animation:pulse 2s infinite
}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}

/* STATS STRIP */
.stats-strip{{
  display:flex;background:#0a0f1e;border-bottom:1px solid var(--border);
}}
.stat-item{{
  flex:1;padding:16px 12px;text-align:center;
  border-right:1px solid var(--border);position:relative
}}
.stat-item:last-child{{border-right:none}}
.stat-n{{font-size:1.8rem;font-weight:800;line-height:1;letter-spacing:-1px}}
.stat-l{{font-size:.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-top:4px}}

/* FILTERS */
.filters{{
  padding:14px 28px;background:#0f1420;
  border-bottom:1px solid var(--border);
  display:flex;gap:8px;flex-wrap:wrap;align-items:center
}}
.f-group{{display:flex;align-items:center;gap:6px}}
.f-label{{font-size:.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;white-space:nowrap}}
.filter-select{{
  background:#1c2333;border:1px solid var(--border);color:var(--text);
  padding:6px 10px;border-radius:8px;font-size:.78rem;cursor:pointer;outline:none;
  transition:border-color .15s
}}
.filter-select:focus{{border-color:var(--blue-dim)}}
.f-divider{{width:1px;height:24px;background:var(--border);margin:0 4px}}
.filter-btn{{
  background:#1c2333;border:1px solid var(--border);color:var(--sub);
  padding:5px 12px;border-radius:20px;font-size:.75rem;cursor:pointer;
  transition:all .15s;white-space:nowrap;font-weight:500
}}
.filter-btn:hover{{border-color:var(--border-light);color:var(--text)}}
.filter-btn.active-blue{{background:#1e3a5f;border-color:var(--blue);color:var(--blue)}}
.filter-btn.active-green{{background:#0d2818;border-color:var(--green-dim);color:var(--green)}}
.filter-btn.active-yellow{{background:#2a1f00;border-color:var(--yellow);color:var(--yellow)}}
.filter-btn.active-red{{background:#2a0a0a;border-color:var(--red);color:var(--red)}}
.btn-reset{{
  background:transparent;border:1px solid var(--muted);color:var(--muted);
  padding:5px 10px;border-radius:8px;font-size:.72rem;cursor:pointer;
  transition:all .15s;margin-left:auto
}}
.btn-reset:hover{{border-color:var(--red);color:var(--red)}}

/* CARDS CONTAINER */
.cards-wrap{{padding:20px 28px;max-width:1000px;margin:0 auto}}
.no-results{{text-align:center;padding:60px;color:var(--muted);font-size:.9rem}}

/* CARD */
.card{{
  background:var(--card);border:1px solid var(--border);
  border-radius:14px;margin-bottom:12px;overflow:hidden;
  transition:border-color .2s,transform .15s;
}}
.card:hover{{border-color:var(--border-light);transform:translateY(-1px)}}
.card.hidden{{display:none}}
.card.finished{{border-left:3px solid var(--green-dim)}}
.card.live-now{{border-left:3px solid var(--yellow);animation:live-glow 3s infinite}}
@keyframes live-glow{{0%,100%{{box-shadow:none}}50%{{box-shadow:0 0 12px #fbbf2420}}}}

/* CARD TOP */
.card-top{{
  display:flex;justify-content:space-between;align-items:center;
  padding:10px 16px 8px;
  background:linear-gradient(90deg,#161b27 0%,#1a1f30 100%);
  border-bottom:1px solid var(--border)
}}
.league-pill{{
  font-size:.71rem;color:var(--sub);font-weight:600;
  display:flex;align-items:center;gap:5px
}}
.card-right{{display:flex;gap:8px;align-items:center}}
.conf-badge{{
  font-size:.65rem;font-weight:800;padding:3px 9px;
  border-radius:20px;letter-spacing:.4px;text-transform:uppercase
}}
.ko-time{{font-size:.71rem;color:var(--muted)}}

/* CARD BODY */
.card-body{{padding:14px 16px}}

/* TEAMS */
.teams-row{{
  display:flex;align-items:center;justify-content:space-between;
  gap:8px;margin-bottom:12px
}}
.team{{font-size:.95rem;font-weight:700;flex:1;line-height:1.2}}
.home-team{{text-align:left}}
.away-team{{text-align:right}}
.score-area{{
  display:flex;flex-direction:column;align-items:center;gap:3px;
  min-width:80px;flex-shrink:0
}}
.predicted-score{{
  background:#1a2040;border:1px solid var(--border-light);
  border-radius:8px;padding:5px 14px;font-size:1rem;
  font-weight:800;color:var(--blue);text-align:center
}}
.score-label{{font-size:.58rem;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}}
.final-score{{
  background:var(--win-bg);border:1px solid var(--win-border);
  border-radius:8px;padding:5px 14px;font-size:1.1rem;
  font-weight:800;color:var(--green);text-align:center
}}

/* TIP */
.tip-row{{margin-bottom:12px}}
.tip-badge{{
  display:inline-flex;align-items:center;gap:5px;
  font-size:.75rem;font-weight:600;color:var(--yellow);
  background:#1f1a00;border:1px solid #3a3000;
  padding:4px 12px;border-radius:6px
}}

/* PROBS */
.probs-row{{display:flex;gap:6px;margin-bottom:10px}}
.prob-col{{
  flex:1;background:#0f1420;border:1px solid var(--border);
  border-radius:10px;padding:10px 8px;text-align:center;
  transition:all .2s
}}
.prob-col.winner{{
  border-color:var(--blue-dim);background:#0d1d3a;
}}
.prob-name{{font-size:.62rem;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:5px}}
.prob-val{{font-size:1.15rem;font-weight:800;color:var(--text)}}
.prob-col.winner .prob-val{{color:var(--blue)}}
.prob-bar{{height:4px;border-radius:2px;background:var(--border);margin-top:6px}}
.prob-bar-fill{{height:100%;border-radius:2px;transition:width .4s}}

/* EXTRA PILLS */
.extra-row{{display:flex;gap:6px;flex-wrap:wrap}}
.extra-pill{{
  display:flex;align-items:center;gap:5px;
  background:#0f1420;border:1px solid var(--border);
  border-radius:8px;padding:5px 11px;font-size:.73rem;color:var(--sub)
}}
.extra-pill span{{font-weight:700;color:var(--text)}}
.extra-pill.hot-green{{border-color:#166534;background:#0d2818;color:var(--green)}}
.extra-pill.hot-green span{{color:var(--green)}}
.extra-pill.hot-blue{{border-color:var(--blue-dim);background:#0d1d3a;color:var(--blue)}}
.extra-pill.hot-blue span{{color:var(--blue)}}

/* TREBLE BANNER */
.treble-banner{{
  background:linear-gradient(135deg,#0b1f3a 0%,#0d1e35 100%);
  border:1px solid #1e4d8c;border-radius:12px;
  padding:14px 20px;margin:14px 28px 0;max-width:1000px;margin-left:auto;margin-right:auto;
  box-shadow:0 0 20px #1e4d8c22
}}
.treble-banner-hdr{{
  display:flex;justify-content:space-between;align-items:center;
  font-size:.82rem;font-weight:700;color:#60a5fa;margin-bottom:10px
}}
.treble-banner-odds{{font-size:.75rem;color:var(--sub);font-weight:400}}
.treble-banner-odds b{{color:var(--text)}}
.treble-link{{color:#60a5fa;text-decoration:none;font-weight:600}}
.treble-link:hover{{text-decoration:underline}}
.tb-pick{{
  display:flex;align-items:center;gap:10px;
  padding:6px 0;border-bottom:1px solid #1a2540;font-size:.78rem;flex-wrap:wrap
}}
.tb-pick:last-child{{border-bottom:none}}
.tb-pick-league{{color:var(--muted);min-width:120px;font-size:.68rem}}
.tb-pick-teams{{flex:1;font-weight:600;color:var(--text);min-width:140px}}
.tb-pick-mkt{{color:var(--sub)}}
.tb-pick-odds{{color:var(--muted)}}

/* FOOTER */
.tabs{{display:flex;background:#0a0f1e;border-bottom:1px solid var(--border);padding:0 28px}}
.tab{{padding:12px 20px;font-size:.82rem;font-weight:600;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;text-decoration:none;transition:all .15s}}
.tab:hover{{color:var(--sub)}}
.tab.active{{color:var(--blue);border-bottom-color:var(--blue)}}
.footer{{
  text-align:center;padding:28px;font-size:.68rem;
  color:var(--muted);border-top:1px solid var(--border);
  margin-top:10px
}}

@media(max-width:580px){{
  .header,.filters,.cards-wrap{{padding-left:14px;padding-right:14px}}
  .team{{font-size:.85rem}}
  .stats-strip .stat-n{{font-size:1.4rem}}
  .filters{{gap:5px}}
}}
</style>
</head>
<body>
<div class="header">
  <div class="header-left">
    <h1>⚽ Matemática Da Bola</h1>
    <div class="meta"><span class="live-dot"></span>Actualizado em {now}</div>
  </div>
</div>
<div class="tabs">
  <a href="dashboard.html" class="tab active">📊 Dashboard</a>
  <a href="backtest.html"  class="tab">🔬 Backtest</a>
</div>
<div class="stats-strip">
  <div class="stat-item"><div class="stat-n" style="color:var(--blue)">{total}</div><div class="stat-l">Jogos hoje</div></div>
  <div class="stat-item"><div class="stat-n" style="color:var(--green)">{high_conf}</div><div class="stat-l">Alta confiança</div></div>
  <div class="stat-item"><div class="stat-n" style="color:var(--yellow)">{sum(1 for e in with_data if confidence_badge(e.get("confidence"))[0]=="MÉDIA")}</div><div class="stat-l">Média confiança</div></div>
  <div class="stat-item"><div class="stat-n" style="color:var(--green-dim)">{sum(1 for e in with_data if e.get("result"))}</div><div class="stat-l">Com resultado</div></div>
</div>
{banner_html}
<div class="filters">
  <div class="f-group">
    <span class="f-label">Liga</span>
    <select class="filter-select" id="f-league" onchange="applyFilters()">
      <option value="">Todas</option>
      {league_opts}
    </select>
  </div>
  <div class="f-group">
    <span class="f-label">Data</span>
    <select class="filter-select" id="f-date" onchange="applyFilters()">
      <option value="">Todas</option>
      {date_opts}
    </select>
  </div>
  <div class="f-divider"></div>
  <div class="f-group">
    <span class="f-label">Confiança</span>
    <button class="filter-btn" id="btn-alta"  onclick="toggleConf('ALTA')" >🟢 Alta</button>
    <button class="filter-btn" id="btn-media" onclick="toggleConf('MÉDIA')">🟡 Média</button>
    <button class="filter-btn" id="btn-baixa" onclick="toggleConf('BAIXA')">🔴 Baixa</button>
  </div>
  <div class="f-divider"></div>
  <div class="f-group">
    <span class="f-label">Mercado</span>
    <button class="filter-btn" id="btn-1x2"  onclick="toggleMarket('1x2')">1X2</button>
    <button class="filter-btn" id="btn-o25"  onclick="toggleMarket('o25')">Over 2.5</button>
    <button class="filter-btn" id="btn-btts" onclick="toggleMarket('btts')">BTTS</button>
    <button class="filter-btn" id="btn-xg"   onclick="toggleMarket('xg')">xG Alto</button>
  </div>
  <button class="btn-reset" onclick="resetFilters()">✕ Limpar</button>
</div>
<div class="cards-wrap" id="cards">
{cards}
<div class="no-results hidden" id="no-results">Nenhum jogo corresponde aos filtros.</div>
</div>
<div class="footer">Matemática Da Bola · {today}</div>
<script>
let activeConf = null;
let activeMarket = null;
const CONF_CLASSES = {{"ALTA":"active-green","MÉDIA":"active-yellow","BAIXA":"active-red"}};
const CONF_MAP = {{"ALTA":"alta","MÉDIA":"media","BAIXA":"baixa"}};
const MKT_BTNS = ["1x2","o25","btts","xg"];
const CONF_BTNS = ["alta","media","baixa"];

function getMarketScore(c) {{
  if (!activeMarket) return 0;
  if (activeMarket === "1x2")  return Math.max(+c.dataset.hw||0, +c.dataset.dr||0, +c.dataset.aw||0);
  if (activeMarket === "o25")  return +c.dataset.o25||0;
  if (activeMarket === "btts") return +c.dataset.btts||0;
  if (activeMarket === "xg")   return +c.dataset.xgtotal||0;
  return 0;
}}

function passesMarketFilter(c) {{
  if (!activeMarket) return true;
  if (activeMarket === "xg")   return (+c.dataset.xgtotal||0) > 0;
  if (activeMarket === "1x2")  return Math.max(+c.dataset.hw||0,+c.dataset.dr||0,+c.dataset.aw||0) >= 61;
  if (activeMarket === "o25")  return (+c.dataset.o25||0) >= 61;
  if (activeMarket === "btts") return (+c.dataset.btts||0) >= 61;
  return true;
}}

function applyFilters() {{
  const league = document.getElementById("f-league").value;
  const date   = document.getElementById("f-date").value;
  const container = document.getElementById("cards");
  const cards = Array.from(document.querySelectorAll(".card"));
  let visible = [];
  cards.forEach(c => {{
    const okL = !league || c.dataset.league === league;
    const okD = !date   || c.dataset.date === date;
    const okC = !activeConf || c.dataset.conf === activeConf;
    const okM = passesMarketFilter(c);
    const show = okL && okD && okC && okM;
    c.classList.toggle("hidden", !show);
    if (show) visible.push(c);
  }});
  if (activeMarket && visible.length > 1) {{
    visible.sort((a,b) => getMarketScore(b) - getMarketScore(a));
    visible.forEach(c => container.appendChild(c));
  }}
  document.getElementById("no-results").classList.toggle("hidden", visible.length > 0);
}}

function toggleConf(val) {{
  const prev = activeConf;
  activeConf = prev === val ? null : val;
  CONF_BTNS.forEach(b => {{
    const el = document.getElementById("btn-"+b);
    el.className = "filter-btn";
  }});
  if (activeConf) {{
    const btn = document.getElementById("btn-"+CONF_MAP[activeConf]);
    btn.classList.add(CONF_CLASSES[activeConf]);
  }}
  applyFilters();
}}

function toggleMarket(val) {{
  const prev = activeMarket;
  activeMarket = prev === val ? null : val;
  MKT_BTNS.forEach(b => document.getElementById("btn-"+b).className = "filter-btn");
  if (activeMarket) document.getElementById("btn-"+activeMarket).classList.add("active-blue");
  applyFilters();
}}

function resetFilters() {{
  document.getElementById("f-league").value = "";
  document.getElementById("f-date").value   = "";
  activeConf = null; activeMarket = null;
  [...CONF_BTNS,...MKT_BTNS].forEach(b => document.getElementById("btn-"+b).className = "filter-btn");
  const container = document.getElementById("cards");
  Array.from(document.querySelectorAll(".card"))
    .sort((a,b) => (a.dataset.date||"") > (b.dataset.date||"") ? 1 : (a.dataset.date||"") < (b.dataset.date||"") ? -1 : (+a.dataset.hour||0) - (+b.dataset.hour||0))
    .forEach(c => {{ c.classList.remove("hidden"); container.appendChild(c); }});
  document.getElementById("no-results").classList.add("hidden");
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

    # Tripla do dia
    treble = load_todays_treble()
    if treble:
        mkt_map = {"BTTS": "BTTS", "1X2-H": "Casa", "1X2-D": "Empate", "1X2-A": "Fora"}
        lines.append("🎯 *TRIPLA DO DIA*")
        for pk in treble["picks"]:
            mkt  = mkt_map.get(pk["market"], pk["market"])
            odds = f" @{pk['odds']:.2f}" if pk.get("odds") else ""
            lines.append(f"  • {pk['league']}: {pk['home']} vs {pk['away']} — {mkt} {int(pk['prob']*100)}%{odds}")
        if treble.get("combined_odds"):
            lines.append(f"  💰 Odds combinadas: *{treble['combined_odds']:.2f}*")
        lines.append("")

    # Value picks
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

        # Resultado final se o jogo já terminou
        result = None
        event_status = event.get("status", "notstarted")
        m["status"] = event_status
        if event_status in ("finished", "inprogress", "live", "halftime"):
            hs = event.get("home_score")
            as_ = event.get("away_score")
            if hs is not None and as_ is not None:
                result = {"home": hs, "away": as_}

        enriched_list.append({"match": m, "pred": pred_norm, "odds": odds, "confidence": conf, "result": result})

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
