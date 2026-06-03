"""
Matemática Da Bola — dashboard BSD API v2
"""

import os
import json
import math
import time
import requests
from html import escape as _he
from collections import Counter
from datetime import datetime, timezone, timedelta

# Probabilidades (escala 0-1)
_CONF_ALTA  = 0.65
_CONF_MEDIA = 0.45
_GA_ID      = "G-WE48R4KL96"
_PAGE_SIZE  = 50

# Labels de mercado para triplas (usados em treble_banner_html e send_telegram)
_MKT_LABEL = {
    "BTTS":  "🔁 BTTS",
    "1X2-H": "🏠 Casa",
    "1X2-D": "🤝 Empate",
    "1X2-A": "✈️ Fora",
}

# Cores de confiança OKLCH (web)
_CONF_COLOR = {
    "ALTA":  "oklch(84% 0.19 80.46)",
    "MÉDIA": "oklch(70% 0.12 188)",
    "BAIXA": "oklch(72% 0.15 35)",
}

# Estilos CSS completos dos badges de confiança (usados em match_card_html)
_BADGE_STYLE = {
    "ALTA":  "background:oklch(10% 0.015 80);color:oklch(84% 0.19 80.46);border:1px solid oklch(61% 0.085 78 / 0.45)",
    "MÉDIA": "background:oklch(7% 0.01 188);color:oklch(70% 0.12 188);border:1px solid oklch(49% 0.08 188 / 0.5)",
    "BAIXA": "background:oklch(7% 0.01 35);color:oklch(72% 0.15 35);border:1px solid oklch(48% 0.1 35 / 0.5)",
}

BSD_KEY  = os.environ["BSD_API_KEY"]
TG_TOKEN = os.environ["TG_TOKEN"]
TG_CHAT  = os.environ["TG_CHAT_ID"]

TREBLES_FILE = "docs/trebles.json"

BASE    = "https://sports.bzzoiro.com/api/v2"
HEADERS = {"Authorization": f"Token {BSD_KEY}"}

_RETRY_DELAYS = [2, 5, 15]

def _log(level, msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {level:5s} {msg}")

# ══════════════════════════════════════════════════════════════════════════════
# CAMADA DE DADOS — BSD API
# ══════════════════════════════════════════════════════════════════════════════

def get(path, params=None):
    # Síncrono com backtest.py — qualquer alteração deve ser replicada
    last_exc = None
    for attempt in range(len(_RETRY_DELAYS) + 1):
        if attempt:
            wait = _RETRY_DELAYS[attempt - 1]
            _log("WARN", f"aguardar {wait}s (tentativa {attempt}/{len(_RETRY_DELAYS)})")
            time.sleep(wait)
        try:
            r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params, timeout=15)
            if r.status_code == 429 or r.status_code >= 500:
                _log("WARN", f"HTTP {r.status_code} — tentativa {attempt+1}/{len(_RETRY_DELAYS)+1}")
                last_exc = requests.exceptions.ConnectionError(f"HTTP {r.status_code}")
                continue
            r.raise_for_status()
            try:
                return r.json()
            except ValueError as e:
                raise requests.exceptions.RequestException(f"JSON inválido: {e}") from e
        except requests.exceptions.RequestException as e:
            last_exc = e
    raise last_exc or requests.exceptions.RequestException("todas as tentativas falharam")

def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def parse_dt(s, context=""):
    if not s:
        return None
    if isinstance(s, str):
        s = s.strip()
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    _log("WARN", f"parse_dt: formato desconhecido '{s}'" + (f" [{context}]" if context else ""))
    return None

def fetch_all_predictions():
    """Busca todas as predicoes disponíveis na API (todos os dias).
    Odds são enriquecidas apenas para hoje em main() — aqui só se pagina."""
    all_preds = []
    offset = 0
    limit = _PAGE_SIZE
    while True:
        try:
            data = get("/predictions/", {"limit": limit, "offset": offset})
            if not isinstance(data, dict):
                _log("WARN", f"fetch_all_predictions: resposta inesperada (offset={offset}): {type(data).__name__}")
                break
            results = data.get("results", [])
            if not results:
                break
            all_preds.extend(results)
            _log("INFO", f"offset={offset} -> {len(results)} predicoes")
            if not data.get("next"):
                break
            offset += limit
        except Exception as e:
            if offset == 0:
                _log("ERR", f"predicoes falhou na primeira pagina: {e}")
                raise
            _log("WARN", f"predicoes offset={offset} falhou — usando {len(all_preds)} ja obtidas: {e}")
            break
    return all_preds

def fetch_odds(event_id):
    try:
        return get(f"/events/{event_id}/odds/comparison/")
    except Exception as e:
        _log("WARN", f"fetch_odds({event_id}): {e}")
        return None

# ══════════════════════════════════════════════════════════════════════════════
# CAMADA DE NEGÓCIO — detecção de value
# ══════════════════════════════════════════════════════════════════════════════

def devig_pinnacle(pin, market, side):
    """Remove a margem Pinnacle antes de calcular edge (de-vig correcto)."""
    if not pin:
        return None
    try:
        if market == "1X2":
            o_h = pin.get("home_odds")
            o_d = pin.get("draw_odds")
            o_a = pin.get("away_odds")
            if not (o_h and o_d and o_a):
                return None
            raw = [1/float(o_h), 1/float(o_d), 1/float(o_a)]
            overround = sum(raw)
            # Overround Pinnacle 1X2 típico: 1.02–1.06; fora desse intervalo = dados suspeitos
            if not (1.01 <= overround <= 1.08):
                _log("WARN", f"overround 1X2 fora do esperado: {overround:.4f}")
                return None
            fair = [p / overround for p in raw]
            return {"HOME": fair[0], "DRAW": fair[1], "AWAY": fair[2]}.get(side)
        if market == "Over2.5":
            o_yes, o_no = pin.get("over_2_5"), pin.get("under_2_5")
        else:  # BTTS
            o_yes, o_no = pin.get("btts_yes"), pin.get("btts_no")
        if not o_yes:
            return None
        implied_yes = 1.0 / float(o_yes)
        # de-vig completo se ambos os lados disponíveis; senão margem típica Pinnacle 2-outcome ~2.5%
        overround = (implied_yes + 1.0/float(o_no)) if o_no else 1.025
        if o_no and not (1.01 <= overround <= 1.06):
            _log("WARN", f"overround {market} fora do esperado: {overround:.4f}")
            return None
        return min(implied_yes / overround, 0.99)
    except (TypeError, ZeroDivisionError, ValueError):
        return None

_EDGE_MIN = {"1X2": 0.07, "Over2.5": 0.05, "BTTS": 0.06}

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
        if ml_prob is None:
            continue
        try:
            ml_p   = float(ml_prob)
            fair_p = devig_pinnacle(pin, market, side)
            if fair_p is None:
                continue
            edge = ml_p - fair_p
            if edge > _EDGE_MIN.get(market, 0.06):
                values.append({
                    "market":    market,
                    "side":      side,
                    "ml_prob":   ml_p,
                    "pin_odds":  float(pin_odds) if pin_odds else None,
                    "fair_prob": round(fair_p, 4),
                    "edge":      edge,
                })
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

def _poisson_over2(lam):
    if lam <= 0:
        return 0.0
    p_le2 = math.exp(-lam) * (1.0 + lam + lam**2 / 2.0)
    return max(0.0, min(1.0, 1.0 - p_le2))

def predict_goals(home_xg, away_xg, btts_prob, o25_prob):
    """
    Combina xG (base) + BTTS (distribuição) para prever golos.
    Algoritmo:
      1. BTTS alto → pull suave do xG total para floor 2.2 (ambas marcam)
      2. Sinal xG → O2.5 via Poisson(lambda=xg_ajustado)
      3. Combinação: O25 modelo (55%) + Poisson xG (45%)
      4. Veredito baseado nos thresholds combinados
    """
    try:
        xgt = float(home_xg or 0) + float(away_xg or 0)
        bp  = float(btts_prob or 0)
        op  = float(o25_prob or 0)
    except (TypeError, ValueError):
        return None
    if xgt <= 0:
        return None

    # Ajuste BTTS: se prob > 55%, pull suave para floor 2.2 (escala 0→1 entre 55% e 95%)
    if bp >= 0.55:
        pull = min((bp - 0.55) / 0.40, 1.0)
        adj = xgt + pull * max(0.0, 2.2 - xgt) * 0.40
    else:
        adj = xgt

    # P(Over 2.5) via Poisson — modelo calibrado vs escala linear
    xg_poisson = _poisson_over2(adj)

    # Combinação: modelo O25 é sinal primário (55%), xG Poisson confirma (45%)
    o25c = round(op * 0.55 + xg_poisson * 0.45, 3)

    low  = max(0, int(adj))
    high = low + 1

    if o25c >= 0.60 and bp >= 0.60:
        verdict, vcol = "Over 2.5 + BTTS",   "oklch(70% 0.12 188)"
    elif o25c >= 0.60:
        verdict, vcol = "Over 2.5 provável",  "oklch(70% 0.12 188)"
    elif bp >= 0.65:
        verdict, vcol = "BTTS provável",       "oklch(84% 0.19 80.46)"
    elif o25c <= 0.38:
        verdict, vcol = "Under 2.5 provável", "oklch(58% 0.15 35)"
    else:
        verdict, vcol = "Inconclusivo",        "oklch(52% 0 0)"

    return {
        "xgt":     round(xgt, 2),
        "adj":     round(adj, 2),
        "range":   f"{low}-{high}",
        "o25":     o25c,
        "btts":    round(bp, 3),
        "verdict": verdict,
        "vcol":    vcol,
    }

def confidence_badge(conf):
    _LABELS = {
        "ALTA":  ("ALTA",  "oklch(84% 0.19 80.46)", "oklch(10% 0.015 80)"),
        "MÉDIA": ("MÉDIA", "oklch(70% 0.12 188)",   "oklch(7% 0.01 188)"),
        "BAIXA": ("BAIXA", "oklch(72% 0.15 35)",    "oklch(7% 0.01 35)"),
    }
    if conf is None:
        return _LABELS["MÉDIA"]
    if isinstance(conf, str) and conf.upper() in _LABELS:
        return _LABELS[conf.upper()]
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return _LABELS["MÉDIA"]
    if c >= _CONF_ALTA:    return _LABELS["ALTA"]
    elif c >= _CONF_MEDIA: return _LABELS["MÉDIA"]
    else:                  return _LABELS["BAIXA"]

def tip_label(hw, dr, aw, o25, conf):
    best = max(hw, dr, aw)
    if best == hw and hw >= 0.55:   return "Vitória Casa"
    elif best == aw and aw >= 0.55: return "Vitória Fora"
    elif best == dr and dr >= 0.35: return "Empate provável"
    elif o25 >= 0.65:               return "Over 2.5 Golos"
    else:                           return "Resultado incerto"

def has_pred_data(pred):
    return bool(pred and pred.get("home_win") is not None)

# ══════════════════════════════════════════════════════════════════════════════
# CAMADA DE APRESENTAÇÃO — geração de HTML
# ══════════════════════════════════════════════════════════════════════════════

def _dashboard_css():
    return """
@import url('https://fonts.googleapis.com/css2?family=Albert+Sans:wght@400;500;600;700;800&family=Alumni+Sans+Pinstripe:wght@400;600&display=swap');

:root{
  --bg:         oklch(7% 0.006 95);
  --surface:    oklch(4% 0.004 95);
  --card:       oklch(11% 0.006 95);
  --card-hover: oklch(13% 0.007 95);
  --graphite:   oklch(15% 0.008 95);
  --graphite2:  oklch(19% 0.008 95);
  --gold:       oklch(84% 0.19 80.46);
  --gold-rich:  oklch(77% 0.13 82);
  --gold-deep:  oklch(61% 0.085 78);
  --gold-pale:  oklch(86% 0.07 84);
  --border:     oklch(78% 0 0 / 0.16);
  --border-s:   oklch(74% 0.09 82 / 0.6);
  --text:       oklch(91% 0 0);
  --text-warm:  oklch(88% 0 0);
  --sub:        oklch(72% 0 0);
  --muted:      oklch(62% 0 0);
  --faint:      oklch(52% 0 0);
  --teal:       oklch(70% 0.12 188);
  --teal-pale:  oklch(82% 0.07 188);
  --teal-deep:  oklch(49% 0.08 188);
  --warn:       oklch(58% 0.15 35);
  --warn-pale:  oklch(72% 0.15 35);
  /* Semantic aliases — backwards-compat */
  --blue:       var(--gold);
  --blue-dim:   var(--gold-rich);
  --green:      var(--teal);
  --green-dim:  var(--teal-deep);
  --yellow:     var(--gold-pale);
  --red:        var(--warn);
  --border-light:oklch(74% 0.09 82 / 0.3);
  --win-bg:     oklch(7% 0.01 188);
  --win-border: var(--teal-deep);
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:"Albert Sans","Segoe UI",system-ui,sans-serif;min-height:100vh}

/* HEADER */
.header{
  background:var(--surface);
  border-bottom:1px solid var(--border);
  padding:22px 28px 18px;
  display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:10px
}
/* h1 uses Alumni Sans Pinstripe, flat Kinpaku Gold — no gradient text */
.header-left h1{
  font-family:"Alumni Sans Pinstripe","Albert Sans",system-ui,sans-serif;
  font-size:1.7rem;font-weight:600;letter-spacing:.02em;
  color:var(--gold)
}
.header-left .meta{font-size:.72rem;color:var(--muted);margin-top:5px}
.live-dot{
  display:inline-block;width:7px;height:7px;border-radius:50%;
  background:var(--teal);margin-right:5px;
  animation:pulse 2s infinite
}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

/* STATS STRIP */
.stats-strip{
  display:flex;background:var(--surface);border-bottom:1px solid var(--border);
}
.stat-item{
  flex:1;padding:16px 12px;text-align:center;
  border-right:1px solid var(--border);position:relative
}
.stat-item:last-child{border-right:none}
.stat-n{font-size:1.8rem;font-weight:800;line-height:1;letter-spacing:-1px}
.stat-l{font-size:.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-top:4px}

/* FILTERS */
.filters{
  padding:14px 28px;background:var(--bg);
  border-bottom:1px solid var(--border);
  display:flex;gap:8px;flex-wrap:wrap;align-items:center
}
.f-group{display:flex;align-items:center;gap:6px}
.f-label{font-size:.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;white-space:nowrap}
.filter-select{
  background:var(--card);border:1px solid var(--border);color:var(--text);
  padding:6px 10px;border-radius:8px;font-size:.78rem;cursor:pointer;outline:none;
  transition:border-color .15s
}
.filter-select:focus{border-color:var(--gold-rich)}
.f-divider{width:1px;height:24px;background:var(--border);margin:0 4px}
.filter-btn{
  background:var(--card);border:1px solid var(--border);color:var(--sub);
  padding:5px 12px;border-radius:20px;font-size:.75rem;cursor:pointer;
  transition:all .15s;white-space:nowrap;font-weight:500
}
.filter-btn:hover{border-color:var(--border-light);color:var(--text)}
.filter-btn.active-blue{background:oklch(10% 0.015 80);border-color:var(--gold);color:var(--gold)}
.filter-btn.active-green{background:oklch(7% 0.01 188);border-color:var(--teal-deep);color:var(--teal)}
.filter-btn.active-yellow{background:oklch(8% 0.014 80);border-color:var(--gold-pale);color:var(--gold-pale)}
.filter-btn.active-red{background:oklch(7% 0.01 35);border-color:var(--warn);color:var(--warn)}
.btn-reset{
  background:transparent;border:1px solid var(--muted);color:var(--muted);
  padding:5px 10px;border-radius:8px;font-size:.72rem;cursor:pointer;
  transition:all .15s;margin-left:auto
}
.btn-reset:hover{border-color:var(--warn);color:var(--warn)}

/* CARDS CONTAINER */
.cards-wrap{padding:20px 28px;max-width:1000px;margin:0 auto}
.no-results{text-align:center;padding:60px;color:var(--muted);font-size:.9rem}

/* CARD — no side-stripe borders (Impeccable ban) */
.card{
  background:var(--card);border:1px solid var(--border);
  border-radius:8px;margin-bottom:12px;overflow:hidden;
  transition:border-color .2s,transform .15s;
}
.card:hover{border-color:var(--border-light);transform:translateY(-1px)}
.card.hidden{display:none}
.card.finished{border-color:oklch(49% 0.08 188 / 0.5)}
.card.live-now{border-color:var(--gold-rich);animation:live-glow 3s infinite}
@keyframes live-glow{0%,100%{box-shadow:none}50%{box-shadow:0 0 12px oklch(84% 0.19 80.46 / 0.12)}}

/* CARD TOP */
.card-top{
  display:flex;justify-content:space-between;align-items:center;
  padding:10px 16px 8px;
  background:var(--graphite);
  border-bottom:1px solid var(--border)
}
.league-pill{
  font-size:.71rem;color:var(--sub);font-weight:600;
  display:flex;align-items:center;gap:5px
}
.card-right{display:flex;gap:8px;align-items:center}
.conf-badge{
  font-size:.65rem;font-weight:800;padding:3px 9px;
  border-radius:4px;letter-spacing:.4px;text-transform:uppercase
}
.ko-time{font-size:.71rem;color:var(--muted)}

/* CARD BODY */
.card-body{padding:14px 16px}

/* TEAMS */
.teams-row{
  display:flex;align-items:center;justify-content:space-between;
  gap:8px;margin-bottom:12px
}
.team{font-size:.95rem;font-weight:700;flex:1;line-height:1.2}
.home-team{text-align:left}
.away-team{text-align:right}
.score-area{
  display:flex;flex-direction:column;align-items:center;gap:3px;
  min-width:80px;flex-shrink:0
}
.predicted-score{
  background:var(--graphite);border:1px solid var(--border);
  border-radius:8px;padding:5px 14px;font-size:1rem;
  font-weight:800;color:var(--gold);text-align:center
}
.score-label{font-size:.58rem;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
.final-score{
  background:var(--win-bg);border:1px solid var(--win-border);
  border-radius:8px;padding:5px 14px;font-size:1.1rem;
  font-weight:800;color:var(--teal);text-align:center
}

/* TIP */
.tip-row{margin-bottom:12px}
.tip-badge{
  display:inline-flex;align-items:center;gap:5px;
  font-size:.75rem;font-weight:600;color:var(--gold);
  background:oklch(8% 0.014 80);border:1px solid oklch(61% 0.085 78 / 0.4);
  padding:4px 12px;border-radius:6px
}

/* PROBS */
.probs-row{display:flex;gap:6px;margin-bottom:10px}
.prob-col{
  flex:1;background:var(--bg);border:1px solid var(--border);
  border-radius:8px;padding:10px 8px;text-align:center;
  transition:all .2s
}
.prob-col.winner{
  border-color:var(--gold-rich);background:oklch(10% 0.015 80);
}
.prob-name{font-size:.62rem;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:5px}
.prob-val{font-size:1.15rem;font-weight:800;color:var(--text)}
.prob-col.winner .prob-val{color:var(--gold)}
.prob-bar{height:4px;border-radius:2px;background:var(--border);margin-top:6px}
.prob-bar-fill{height:100%;border-radius:2px;transition:width .4s}

/* EXTRA PILLS */
.extra-row{display:flex;gap:6px;flex-wrap:wrap}
.extra-pill{
  display:flex;align-items:center;gap:5px;
  background:var(--bg);border:1px solid var(--border);
  border-radius:8px;padding:5px 11px;font-size:.73rem;color:var(--sub)
}
.extra-pill span{font-weight:700;color:var(--text)}
.extra-pill.hot-green{border-color:var(--teal-deep);background:var(--win-bg);color:var(--teal)}
.extra-pill.hot-green span{color:var(--teal)}
.extra-pill.hot-blue{border-color:var(--gold-rich);background:oklch(10% 0.015 80);color:var(--gold)}
.extra-pill.hot-blue span{color:var(--gold)}

/* TREBLE BANNER */
.treble-banner{
  background:oklch(8% 0.014 80);
  border:1px solid oklch(61% 0.085 78 / 0.4);border-radius:8px;
  padding:14px 20px;margin:14px 28px 0;max-width:1000px;margin-left:auto;margin-right:auto
}
.treble-banner-hdr{
  display:flex;justify-content:space-between;align-items:center;
  font-size:.82rem;font-weight:700;color:var(--gold);margin-bottom:10px
}
.treble-banner-odds{font-size:.75rem;color:var(--sub);font-weight:400}
.treble-banner-odds b{color:var(--text)}
.treble-link{color:var(--gold);text-decoration:none;font-weight:600}
.treble-link:hover{text-decoration:underline}
.tb-pick{
  display:flex;align-items:center;gap:10px;
  padding:6px 0;border-bottom:1px solid var(--border);font-size:.78rem;flex-wrap:wrap
}
.tb-pick:last-child{border-bottom:none}
.tb-pick-num{width:18px;height:18px;border-radius:50%;background:oklch(11% 0.02 80);color:var(--gold);font-size:.65rem;font-weight:800;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.tb-pick-league{color:var(--muted);min-width:120px;font-size:.68rem}
.tb-pick-teams{flex:1;font-weight:600;color:var(--text);min-width:140px}
.tb-pick-mkt{color:var(--sub)}
.tb-pick-odds{color:var(--muted)}

/* GOAL PREDICTION */
.gp-row{
  display:flex;align-items:center;gap:8px;flex-wrap:wrap;
  margin-top:8px;padding:7px 10px;
  background:var(--surface);border:1px solid var(--border);border-radius:8px
}
.gp-lbl{font-size:.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;flex-shrink:0}
.gp-range{font-size:.85rem;font-weight:800;color:var(--text);flex-shrink:0;min-width:28px}
.gp-bar-bg{flex:1;height:6px;background:var(--border);border-radius:3px;min-width:40px}
.gp-bar-fill{height:100%;border-radius:3px;transition:width .4s}
.gp-pct{font-size:.72rem;font-weight:700;flex-shrink:0;min-width:30px;text-align:right}
.gp-verdict{font-size:.72rem;font-weight:600;flex-shrink:0}

/* FOOTER */
.tabs{display:flex;background:var(--surface);border-bottom:1px solid var(--border);padding:0 28px}
.tab{padding:12px 20px;font-size:.82rem;font-weight:600;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;text-decoration:none;transition:all .15s}
.tab:hover{color:var(--sub)}
.tab.active{color:var(--gold);border-bottom-color:var(--gold)}
.footer{
  text-align:center;padding:28px;font-size:.68rem;
  color:var(--muted);border-top:1px solid var(--border);
  margin-top:10px
}

@media(max-width:580px){
  .header,.filters,.cards-wrap{padding-left:14px;padding-right:14px}
  .team{font-size:.85rem}
  .stats-strip .stat-n{font-size:1.4rem}
  .filters{gap:5px}
}
"""

def _dashboard_js():
    return """
let activeConf = null;
let activeMarket = null;
const CONF_CLASSES = {"ALTA":"active-green","MÉDIA":"active-yellow","BAIXA":"active-red"};
const CONF_MAP = {"ALTA":"alta","MÉDIA":"media","BAIXA":"baixa"};
const MKT_BTNS = ["1x2","o25","btts","xg"];
const CONF_BTNS = ["alta","media","baixa"];

function getMarketScore(c) {
  if (!activeMarket) return 0;
  if (activeMarket === "1x2")  return Math.max(+c.dataset.hw||0, +c.dataset.dr||0, +c.dataset.aw||0);
  if (activeMarket === "o25")  return +c.dataset.o25||0;
  if (activeMarket === "btts") return +c.dataset.btts||0;
  if (activeMarket === "xg")   return +c.dataset.xgtotal||0;
  return 0;
}

function passesMarketFilter(c) {
  if (!activeMarket) return true;
  if (activeMarket === "xg")   return (+c.dataset.xgtotal||0) > 0;
  if (activeMarket === "1x2")  return Math.max(+c.dataset.hw||0,+c.dataset.dr||0,+c.dataset.aw||0) >= 61;
  if (activeMarket === "o25")  return (+c.dataset.o25||0) >= 61;
  if (activeMarket === "btts") return (+c.dataset.btts||0) >= 61;
  return true;
}

function applyFilters() {
  const league = document.getElementById("f-league").value;
  const date   = document.getElementById("f-date").value;
  const container = document.getElementById("cards");
  const cards = Array.from(document.querySelectorAll(".card"));
  let visible = [];
  cards.forEach(c => {
    const okL = !league || c.dataset.league === league;
    const okD = !date   || c.dataset.date === date;
    const okC = !activeConf || c.dataset.conf === activeConf;
    const okM = passesMarketFilter(c);
    const show = okL && okD && okC && okM;
    c.classList.toggle("hidden", !show);
    if (show) visible.push(c);
  });
  if (activeMarket && visible.length > 1) {
    visible.sort((a,b) => getMarketScore(b) - getMarketScore(a));
    visible.forEach(c => container.appendChild(c));
  }
  document.getElementById("no-results").classList.toggle("hidden", visible.length > 0);
}

function toggleConf(val) {
  const prev = activeConf;
  activeConf = prev === val ? null : val;
  CONF_BTNS.forEach(b => {
    const el = document.getElementById("btn-"+b);
    el.className = "filter-btn";
  });
  if (activeConf) {
    const btn = document.getElementById("btn-"+CONF_MAP[activeConf]);
    btn.classList.add(CONF_CLASSES[activeConf]);
  }
  applyFilters();
}

function toggleMarket(val) {
  const prev = activeMarket;
  activeMarket = prev === val ? null : val;
  MKT_BTNS.forEach(b => document.getElementById("btn-"+b).className = "filter-btn");
  if (activeMarket) document.getElementById("btn-"+activeMarket).classList.add("active-blue");
  applyFilters();
}

function resetFilters() {
  document.getElementById("f-league").value = "";
  document.getElementById("f-date").value   = "";
  activeConf = null; activeMarket = null;
  [...CONF_BTNS,...MKT_BTNS].forEach(b => document.getElementById("btn-"+b).className = "filter-btn");
  const container = document.getElementById("cards");
  Array.from(document.querySelectorAll(".card"))
    .sort((a,b) => (a.dataset.date||"") > (b.dataset.date||"") ? 1 : (a.dataset.date||"") < (b.dataset.date||"") ? -1 : (+a.dataset.hour||0) - (+b.dataset.hour||0))
    .forEach(c => { c.classList.remove("hidden"); container.appendChild(c); });
  document.getElementById("no-results").classList.add("hidden");
}

document.addEventListener("DOMContentLoaded", applyFilters);
"""

def match_card_html(enriched):
    m      = enriched["match"]
    pred   = enriched["pred"]
    conf   = enriched.get("confidence")
    result = enriched.get("result")

    home   = _he(m.get("home_team", "?") or "?")
    away   = _he(m.get("away_team", "?") or "?")
    league = _he(m.get("_league_name", "") or "")
    flag   = league_flag(m.get("_league_name", ""))
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
    except (TypeError, ValueError): xg_total = 0

    conf_label, _, _ = confidence_badge(conf)
    tip  = tip_label(hw, dr, aw, o25, conf)
    best = max(hw, dr, aw)

    status = m.get("status", "notstarted")
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

    badge_style = _BADGE_STYLE.get(conf_label, _BADGE_STYLE["BAIXA"])

    def bar(p, highlight):
        color = "oklch(84% 0.19 80.46)" if highlight else "oklch(78% 0 0 / 0.16)"
        return f'<div class="prob-bar"><div class="prob-bar-fill" style="width:{int(p*100)}%;background:{color}"></div></div>'

    o25_class = "extra-pill hot-green" if o25 >= 0.61 else "extra-pill"
    bt_class  = "extra-pill hot-green" if bt  >= 0.61 else "extra-pill"
    xg_class  = "extra-pill hot-blue"  if xg_total >= 2.5 else "extra-pill"

    gp = predict_goals(xg_h if xg_h != "–" else None,
                       xg_a if xg_a != "–" else None, bt, o25)
    if gp:
        bar_w = int(gp["o25"] * 100)
        bar_col = gp["vcol"]
        goal_html = (
            f'<div class="gp-row">'
            f'<span class="gp-lbl">🎯 Golos</span>'
            f'<span class="gp-range">{gp["range"]}</span>'
            f'<div class="gp-bar-bg"><div class="gp-bar-fill" style="width:{bar_w}%;background:{bar_col}"></div></div>'
            f'<span class="gp-pct" style="color:{bar_col}">{bar_w}%</span>'
            f'<span class="gp-verdict" style="color:{bar_col}">{gp["verdict"]}</span>'
            f'</div>'
        )
    else:
        goal_html = ""

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
        {goal_html}
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
    pick_parts = []
    for i, pk in enumerate(treble.get("picks", []), 1):
        mkt_key = pk.get("market", "")
        col  = _CONF_COLOR.get(pk.get("conf", ""), "oklch(62% 0 0)")
        mkt  = _MKT_LABEL.get(mkt_key, mkt_key or "?")
        odds = f"@{pk['odds']:.2f}" if pk.get("odds") else ""
        conf_bg = {"ALTA": "oklch(10% 0.015 80)", "MÉDIA": "oklch(7% 0.01 188)", "BAIXA": "oklch(7% 0.01 35)"}.get(pk.get("conf",""), "oklch(8% 0.006 95)")
        conf_bd = {"ALTA": "oklch(61% 0.085 78 / 0.45)", "MÉDIA": "oklch(49% 0.08 188 / 0.5)", "BAIXA": "oklch(48% 0.1 35 / 0.5)"}.get(pk.get("conf",""), "oklch(52% 0 0 / 0.3)")
        pick_parts.append(
            f'<div class="tb-pick">'
            f'<span class="tb-pick-num">{i}</span>'
            f'<span class="tb-pick-league">{pk.get("league", "")}</span>'
            f'<span class="tb-pick-teams">{pk.get("home", "?")} <span style="color:var(--muted)">vs</span> {pk.get("away", "?")}</span>'
            f'<span class="tb-pick-mkt">{mkt}</span>'
            f'<span style="color:{col};font-weight:700">{int((pk.get("prob") or 0) * 100)}%</span>'
            f'<span class="tb-pick-conf" style="background:{conf_bg};color:{col};border:1px solid {conf_bd}">{pk.get("conf","")}</span>'
            f'<span class="tb-pick-odds">{odds}</span>'
            f'</div>'
        )
    picks_html = "".join(pick_parts)
    combined = f"{treble['combined_odds']:.2f}" if treble.get("combined_odds") else "–"
    return (
        f'<div class="treble-banner">'
        f'<div class="treble-banner-hdr">'
        f'<span>🎯 Tripla do Dia — {treble.get("date", "")}</span>'
        f'<span class="treble-banner-odds"><a href="backtest.html" class="treble-link">Histórico &amp; ROI →</a></span>'
        f'</div>'
        f'{picks_html}'
        f'</div>'
    )

def build_html(enriched_list, todays_treble=None):
    today = today_str()
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    with_data = [e for e in enriched_list if has_pred_data(e["pred"])]
    banner_html   = treble_banner_html(todays_treble)

    leagues  = sorted(set(e["match"].get("_league_name","") for e in with_data))
    dates    = sorted(set(
        e["match"].get("event_date","")[:10]
        for e in with_data if e["match"].get("event_date")
    ))

    # Data padrão: hoje se houver jogos, senão a primeira data disponível
    default_date = today if today in dates else (dates[0] if dates else today)

    # Contadores do header: jogos da data padrão
    today_data  = [e for e in with_data if e["match"].get("event_date","")[:10] == default_date]
    conf_counts = Counter(confidence_badge(e.get("confidence"))[0] for e in today_data)
    high_conf   = conf_counts["ALTA"]
    med_conf    = conf_counts["MÉDIA"]
    low_conf    = conf_counts["BAIXA"]
    total       = len(today_data)

    league_opts = "".join(f'<option value="{_he(l)}">{league_flag(l)} {_he(l)}</option>' for l in leagues)
    date_opts   = "".join(
        f'<option value="{_he(d)}" {"selected" if d == default_date else ""}>{_he(d)}</option>'
        for d in dates
    )

    cards = "\n".join(match_card_html(e) for e in with_data)

    return f'''<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Matemática Da Bola — {today}</title>
<script async src="https://www.googletagmanager.com/gtag/js?id={_GA_ID}"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments)}}gtag("js",new Date());gtag("config","{_GA_ID}");</script>
<style>{_dashboard_css()}</style>
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
  <div class="stat-item"><div class="stat-n" style="color:var(--yellow)">{med_conf}</div><div class="stat-l">Média confiança</div></div>
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
<script>{_dashboard_js()}</script>
</body>
</html>'''

# ══════════════════════════════════════════════════════════════════════════════
# CAMADA DE NOTIFICAÇÕES — Telegram
# ══════════════════════════════════════════════════════════════════════════════

def escape_md(s):
    """Escapa caracteres especiais do Markdown v1 do Telegram em valores dinâmicos."""
    for ch in ('_', '*', '`', '['):
        s = str(s).replace(ch, f'\\{ch}')
    return s

def send_telegram(enriched_list, todays_treble=None):
    today  = today_str()
    blocks = []

    # Filtrar para a data padrão: hoje se houver jogos, senão a primeira disponível
    dates_with_games = sorted(set(
        e["match"].get("event_date", "")[:10]
        for e in enriched_list if e["match"].get("event_date")
    ))
    tg_date = today if today in dates_with_games else (dates_with_games[0] if dates_with_games else today)
    enriched_list = [
        e for e in enriched_list
        if e["match"].get("event_date", "")[:10] == tg_date
    ]

    # ── 1. Tripla do dia ──────────────────────────────────────────────────────
    treble  = todays_treble
    if treble:
        picks_lines = []
        for i, pk in enumerate(treble.get("picks", []), 1):
            conf    = pk.get("conf", "")
            mkt_key = pk.get("market", "")
            mkt     = _MKT_LABEL.get(mkt_key, mkt_key or "?")
            odds    = f" @{pk['odds']:.2f}" if pk.get("odds") else ""
            flag    = league_flag(pk.get("league", ""))
            picks_lines.append(
                f"`{i}` {flag} *{escape_md(pk.get('home', '?'))} vs {escape_md(pk.get('away', '?'))}*\n"
                f"   {mkt} · {int((pk.get('prob') or 0)*100)}% · _{conf}_{odds}"
            )
        combined = f"\n💰 Odds combinadas: *{treble['combined_odds']:.2f}*" if treble.get("combined_odds") else ""
        blocks.append("🎯 *TRIPLA DO DIA*\n" + "\n\n".join(picks_lines) + combined)
    else:
        blocks.append("🎯 *TRIPLA DO DIA*\n_Picks insuficientes hoje._")

    # ── 2. xG do dia — candidatos Over 2.5 ───────────────────────────────────
    xg_candidates = []
    for e in enriched_list:
        pred = e.get("pred", {}) or {}
        conf_val = e.get("confidence")
        if conf_val is None:
            continue
        try:
            conf_f = float(conf_val)
        except (TypeError, ValueError):
            continue
        if conf_f < 0.45:   # só ALTA e MÉDIA
            continue
        hx = pred.get("home_xg") or 0
        ax = pred.get("away_xg") or 0
        xgt = round(float(hx) + float(ax), 2)
        if xgt < 2.9:
            continue
        conf_lbl = "ALTA" if conf_f >= 0.65 else "MÉDIA"
        m = e["match"]
        gp = predict_goals(hx, ax, pred.get("btts_yes") or 0, pred.get("over_2_5") or 0)
        xg_candidates.append({
            "xgt":    xgt,
            "conf":   conf_lbl,
            "league": m.get("_league_name", ""),
            "home":   m.get("home_team", "?"),
            "away":   m.get("away_team", "?"),
            "gp":     gp,
        })

    xg_candidates.sort(key=lambda x: -x["xgt"])
    if xg_candidates:
        xg_lines = []
        for c in xg_candidates[:5]:
            flag = league_flag(c["league"])
            gp   = c["gp"]
            verd = f" · _{gp['verdict']}_" if gp else ""
            rng  = f" · {gp['range']} golos" if gp else ""
            xg_lines.append(
                f"{flag} *{escape_md(c['home'])} vs {escape_md(c['away'])}*\n"
                f"   xG: *{c['xgt']}*{rng}{verd}"
            )
        blocks.append("📈 *xG ELEVADO — Over 2.5*\n" + "\n\n".join(xg_lines))

    # ── 3. Value detectado (confiança ALTA; thresholds: 1X2=7%, BTTS=6%, Over2.5=5%) ──
    _mkt_tg = {"BTTS": "🔁 BTTS", "Over2.5": "📈 Over 2.5",
               "1X2": {"HOME": "🏠 1X2 Casa", "DRAW": "🤝 1X2 Empate", "AWAY": "✈️ 1X2 Fora"}}
    strong_value = []
    for e in enriched_list:
        conf_val = e.get("confidence")
        try:
            if conf_val is None or float(conf_val) < 0.65:
                continue
        except (TypeError, ValueError):
            continue
        vals = detect_value(e["pred"], e["odds"])
        for v in vals:
            m = e["match"]
            strong_value.append({
                "league": m.get("_league_name", ""),
                "home":   m.get("home_team", "?"),
                "away":   m.get("away_team", "?"),
                **v,
            })

    strong_value.sort(key=lambda x: -x["edge"])
    if strong_value:
        val_lines = []
        for v in strong_value[:5]:
            flag = league_flag(v["league"])
            mkt_entry = _mkt_tg.get(v["market"], v["market"])
            mkt = mkt_entry.get(v.get("side", ""), v["market"]) if isinstance(mkt_entry, dict) else mkt_entry
            odds_str = f" @{v['pin_odds']:.2f}" if v.get("pin_odds") else ""
            val_lines.append(
                f"{flag} *{escape_md(v['home'])} vs {escape_md(v['away'])}*\n"
                f"   {mkt} · ML {v['ml_prob']*100:.0f}% · fair {v['fair_prob']*100:.1f}%{odds_str} · edge *+{v['edge']*100:.1f}%*"
            )
        blocks.append("💎 *VALUE DETECTADO*\n" + "\n\n".join(val_lines))

    # ── Enviar em mensagens separadas (cada bloco = 1 msg) ───────────────────
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"

    def _tg_send(text):
        for attempt in range(3):
            try:
                r = requests.post(url, json={"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"}, timeout=10)
                r.raise_for_status()
                return
            except Exception as e:
                if attempt < 2:
                    time.sleep(5)
                else:
                    _log("WARN", f"telegram falhou após {attempt+1} tentativas: {e}")

    header = f"⚽ *Matemática Da Bola — {today}* · {len(enriched_list)} jogos\n"
    _tg_send(header)
    for block in blocks:
        _tg_send(block)

# ══════════════════════════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ══════════════════════════════════════════════════════════════════════════════

def main():
    today = today_str()
    _log("INFO", f"{today} — a buscar predicoes...")

    # 1. Buscar todas as predicoes do dia de uma vez (paginadas)
    all_preds = fetch_all_predictions()
    _log("INFO", f"{len(all_preds)} predicoes encontradas")

    # 2. Para cada predicao, buscar odds e montar enriched
    enriched_list = []
    failed_count  = 0
    seen = set()
    for pred in all_preds:
        event = pred.get("event") or {}
        eid = event.get("id")
        if eid in seen:
            continue
        seen.add(eid)

        try:
            # Construir estrutura de match compativel com o resto do codigo
            m = {
                "id":           eid,
                "home_team":    event.get("home_team"),
                "away_team":    event.get("away_team"),
                "event_date":   event.get("event_date"),
                "_league_name": event.get("league_name", "?"),
            }

            # Normalizar pred para o formato esperado pelo match_card_html
            markets = pred.get("markets") or {}
            mr  = markets.get("match_result") or {}
            xg  = markets.get("expected_goals") or {}
            ou  = markets.get("over_under") or {}
            bt  = markets.get("btts") or {}
            sc  = markets.get("score") or {}

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

            # Odds Pinnacle apenas para jogos de hoje (value detection relevante só hoje)
            event_date = event.get("event_date", "")[:10]
            odds = fetch_odds(eid) if event_date == today else None
            home = m.get("home_team", "?")
            away = m.get("away_team", "?")
            league = m.get("_league_name", "")
            _log("INFO", f"{home} vs {away} [{league}]")
            conf = (pred.get("model") or {}).get("confidence")

            # Resultado final se o jogo já terminou
            result = None
            event_status = event.get("status", "notstarted")
            m["status"] = event_status
            if event_status in ("finished", "inprogress", "live", "halftime"):
                hs = event.get("home_score")
                as_ = event.get("away_score")
                if hs is not None and as_ is not None:
                    result = {"home": hs, "away": as_}
        except Exception as e:
            failed_count += 1
            _log("WARN", f"evento {eid} falhou — a saltar: {e}")
            time.sleep(0.5)
            continue

        time.sleep(0.5)
        enriched_list.append({"match": m, "pred": pred_norm, "odds": odds, "confidence": conf, "result": result})

    # 3. Ordenar por hora de kickoff
    enriched_list.sort(key=lambda e: e["match"].get("event_date", ""))

    today_count = sum(1 for e in enriched_list if e["match"].get("event_date","")[:10] == today)
    other_count = len(enriched_list) - today_count
    _log("INFO", f"{len(enriched_list)} jogos OK, {failed_count} falharam ({today_count} hoje, {other_count} outros dias)")

    if seen and len(enriched_list) < len(seen) * 0.5:
        raise RuntimeError(f"Demasiadas falhas: {failed_count}/{len(seen)} eventos únicos — a abortar para evitar dashboard vazio")

    todays_treble = load_todays_treble()
    html = build_html(enriched_list, todays_treble)
    os.makedirs("docs", exist_ok=True)
    _tmp = "docs/dashboard.html.tmp"
    with open(_tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(_tmp, "docs/dashboard.html")
    _log("INFO", "docs/dashboard.html guardado")

    send_telegram(enriched_list, todays_treble)
    _log("INFO", "Telegram enviado ✓")

if __name__ == "__main__":
    main()

