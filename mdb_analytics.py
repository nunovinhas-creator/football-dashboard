"""
Matemática Da Bola — Camada de Análise (ROI · CLV · Value · Out-of-Sample)
═══════════════════════════════════════════════════════════════════════════
Módulo autónomo. Lê docs/history.json e produz as métricas que faltavam:

  Ponto 1 — ROI por mercado nos SINGLES (BTTS, Over 2.5, 1X2) usando as odds
            Pinnacle já guardadas em cada registo. Hit rate sozinho não diz
            se ganhas dinheiro — isto diz.
  Ponto 2 — CLV (Closing Line Value). Precisa das odds no momento da aposta
            (-30min) E das odds de fecho. Funções prontas; degradam com
            elegância enquanto os dados não existem. Ver _CLV_SETUP no fim.
  Ponto 3 — Value unificado. Reconstrói as MESMAS apostas que o detect_value
            sinaliza (edge vs Pinnacle de-vigged) a partir do histórico e
            faz-lhes backtest + ROI. Deixa de medir uma coisa e apostar outra.
  Ponto 4 — Congelamento + out-of-sample. FROZEN_SINCE marca a data a partir
            da qual tudo é OOS. Todas as métricas aceitam since=FROZEN_SINCE
            para reportar só o forward test honesto.

Uso:
    python mdb_analytics.py                 # relatório completo (texto)
    python mdb_analytics.py --oos           # só out-of-sample (>= FROZEN_SINCE)

Para integrar no email/HTML do backtest.py:
    from mdb_analytics import market_roi, value_roi, clv_stats, sample_split
"""

import os
import sys
import json
import math
from datetime import datetime, timezone

HISTORY_FILE = os.environ.get("MDB_HISTORY", "docs/history.json")
FREEZE_MANIFEST = "docs/freeze_manifest.json"

# ── PONTO 4 ────────────────────────────────────────────────────────────────
# Data de congelamento. Tudo com date >= FROZEN_SINCE é out-of-sample (OOS):
# dados que os thresholds abaixo NUNCA viram quando foram calibrados.
# Regra: se mudares qualquer threshold, avanças esta data para hoje.
FROZEN_SINCE = "2026-06-10"

THRESHOLDS = {
    "BTTS_MIN":  61,     # prob BTTS mínima (escala 0-100)
    "X12_MIN":   61,     # melhor prob 1X2 mínima (escala 0-100)
    "O25_XG":    2.9,    # xG total mínimo para Over 2.5
    "EDGE_1X2":  0.07,   # edge mínimo vs Pinnacle fair
    "EDGE_O25":  0.05,
    "EDGE_BTTS": 0.06,
    "PIN_2WAY_MARGIN": 1.025,  # margem assumida quando só há 1 lado (de-vig parcial)
}

# ═══════════════════════════════════════════════════════════════════════════
# ESTATÍSTICA
# ═══════════════════════════════════════════════════════════════════════════

def wilson_ci(hits, n, z=1.96):
    """Intervalo de confiança Wilson 95% (em %)."""
    if n == 0:
        return 0.0, 100.0
    p = hits / n
    center = (p + z*z/(2*n)) / (1 + z*z/n)
    half   = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
    return max(0.0, round((center - half)*100, 1)), min(100.0, round((center + half)*100, 1))

def breakeven_odds(rate_pct):
    """Odds mínimas para lucro dado um hit rate (%)."""
    if rate_pct <= 0:
        return None
    return round(100.0 / rate_pct, 2)

# ═══════════════════════════════════════════════════════════════════════════
# DE-VIG (idêntico ao dashboard.py para coerência)
# ═══════════════════════════════════════════════════════════════════════════

def _devig_1x2(o_h, o_d, o_a):
    try:
        o_h, o_d, o_a = float(o_h), float(o_d), float(o_a)
        raw = [1/o_h, 1/o_d, 1/o_a]
        over = sum(raw)
        if not (1.01 <= over <= 1.08):
            return None
        return {"H": raw[0]/over, "D": raw[1]/over, "A": raw[2]/over}
    except (TypeError, ValueError, ZeroDivisionError):
        return None

def _devig_2way(o_yes, margin=None):
    """De-vig parcial: só o lado 'sim' está guardado, aplica margem típica."""
    try:
        o_yes = float(o_yes)
        m = margin or THRESHOLDS["PIN_2WAY_MARGIN"]
        return min((1.0/o_yes) / m, 0.99)
    except (TypeError, ValueError, ZeroDivisionError):
        return None

# ═══════════════════════════════════════════════════════════════════════════
# PONTO 1 — ROI POR MERCADO (SINGLES)
# ═══════════════════════════════════════════════════════════════════════════

def _single_legs(rec):
    """
    Devolve as pernas single deste registo no formato:
        (mercado, foi_pick, acertou, odds_pinnacle)
    Lê apenas campos JÁ guardados pelo make_record().
    """
    legs = []

    # BTTS
    legs.append((
        "BTTS",
        bool(rec.get("pick_btts")),
        bool(rec.get("hit_btts")),
        rec.get("pin_btts"),
    ))
    # Over 2.5
    legs.append((
        "Over2.5",
        bool(rec.get("pick_o25")),
        bool(rec.get("hit_o25")),
        rec.get("pin_o25"),
    ))
    # 1X2 — odds do lado previsto pelo modelo
    side = rec.get("pred")  # "H" / "D" / "A"
    odds_1x2 = {"H": rec.get("pin_home"), "D": rec.get("pin_draw"),
                "A": rec.get("pin_away")}.get(side)
    legs.append((
        "1X2",
        bool(rec.get("pick_1x2")),
        bool(rec.get("hit_1x2")),
        odds_1x2,
    ))
    return legs

def _roi_block(rows):
    """rows = lista de (acertou, odds|None). Stake fixo 1u."""
    picks = len(rows)
    hits  = sum(1 for hit, _ in rows if hit)
    rate  = round(hits / picks * 100, 1) if picks else 0.0
    ci_lo, ci_hi = wilson_ci(hits, picks)

    priced = [(hit, float(od)) for hit, od in rows if od]
    staked = float(len(priced))
    returned = round(sum(od for hit, od in priced if hit), 2)
    profit = round(returned - staked, 2) if staked else None
    roi    = round(profit / staked * 100, 1) if staked else None
    avg_od = round(sum(od for _, od in priced) / len(priced), 2) if priced else None
    be     = breakeven_odds(rate)

    return {
        "picks": picks, "hits": hits, "rate": rate,
        "ci_lo": ci_lo, "ci_hi": ci_hi,
        "priced": len(priced), "staked": staked, "returned": returned,
        "profit_u": profit, "roi_pct": roi, "avg_odds": avg_od,
        "breakeven_odds": be,
    }

def market_roi(records, since=None):
    """
    ROI por mercado single. Devolve {mercado: bloco_roi}.
    O hit rate é calculado sobre TODOS os picks; o ROI só sobre os que têm
    odds guardadas (priced). 'breakeven_odds' = odds mínimas para o hit rate
    dar lucro — se avg_odds < breakeven, estás a perder mesmo acertando muito.
    """
    recs = _filter(records, since)
    buckets = {"BTTS": [], "Over2.5": [], "1X2": []}
    for r in recs:
        for market, picked, hit, odds in _single_legs(r):
            if picked:
                buckets[market].append((hit, odds))
    return {m: _roi_block(rows) for m, rows in buckets.items()}

# ═══════════════════════════════════════════════════════════════════════════
# PONTO 3 — VALUE UNIFICADO (reconstrói os picks do detect_value)
# ═══════════════════════════════════════════════════════════════════════════

def value_picks(records, since=None):
    """
    Reconstrói as apostas de VALUE (edge vs Pinnacle) a partir do histórico,
    usando exactamente a mesma lógica do detect_value() do dashboard:
        edge = ml_prob - fair_prob  →  flag se edge > threshold do mercado.
    Devolve lista de picks com edge, odds e se acertou. Assim medimos as
    apostas que realmente sinalizamos, não thresholds paralelos.
    """
    recs = _filter(records, since)
    out = []
    for r in recs:
        # probabilidades ML guardadas em escala 0-100
        ph, pd, pa = r.get("ph", 0)/100, r.get("pd", 0)/100, r.get("pa", 0)/100
        po, pb     = r.get("po", 0)/100, r.get("pb", 0)/100

        # ── 1X2: de-vig completo (temos os 3 lados) ──
        fair = _devig_1x2(r.get("pin_home"), r.get("pin_draw"), r.get("pin_away"))
        if fair:
            for side, ml, odkey, hitfn in [
                ("H", ph, "pin_home", lambda r: r.get("real") == "H"),
                ("D", pd, "pin_draw", lambda r: r.get("real") == "D"),
                ("A", pa, "pin_away", lambda r: r.get("real") == "A"),
            ]:
                edge = ml - fair[side]
                if edge > THRESHOLDS["EDGE_1X2"]:
                    out.append(_vpick(r, "1X2", side, ml, fair[side], edge,
                                      r.get(odkey), hitfn(r)))

        # ── Over 2.5 ──
        fo = _devig_2way(r.get("pin_o25"))
        if fo is not None and (po - fo) > THRESHOLDS["EDGE_O25"]:
            out.append(_vpick(r, "Over2.5", "OVER", po, fo, po - fo,
                              r.get("pin_o25"), r.get("goals", 0) > 2))

        # ── BTTS ──
        fb = _devig_2way(r.get("pin_btts"))
        if fb is not None and (pb - fb) > THRESHOLDS["EDGE_BTTS"]:
            hit = r.get("hs", 0) > 0 and r.get("as", 0) > 0
            out.append(_vpick(r, "BTTS", "YES", pb, fb, pb - fb,
                              r.get("pin_btts"), hit))
    return out

def _vpick(r, market, side, ml, fair, edge, odds, hit):
    return {
        "date": r.get("date"), "league": r.get("league"),
        "home": r.get("home"), "away": r.get("away"),
        "market": market, "side": side,
        "ml_prob": round(ml, 4), "fair_prob": round(fair, 4),
        "edge": round(edge, 4),
        "odds": float(odds) if odds else None,
        "hit": bool(hit),
    }

def value_roi(records, since=None):
    """ROI das apostas de value — total e por mercado."""
    picks = value_picks(records, since)
    overall = _roi_block([(p["hit"], p["odds"]) for p in picks])
    by_market = {}
    for m in ("1X2", "Over2.5", "BTTS"):
        rows = [(p["hit"], p["odds"]) for p in picks if p["market"] == m]
        if rows:
            by_market[m] = _roi_block(rows)
    avg_edge = round(sum(p["edge"] for p in picks) / len(picks) * 100, 2) if picks else None
    return {"overall": overall, "by_market": by_market,
            "n": len(picks), "avg_edge_pct": avg_edge}

# ═══════════════════════════════════════════════════════════════════════════
# PONTO 2 — CLV (Closing Line Value)
# ═══════════════════════════════════════════════════════════════════════════
# Precisa de dois campos NOVOS por mercado em cada registo:
#   bet_pin_<side>   = odds no momento da decisão (-30min, "APOSTA AGORA")
#   close_pin_<side> = últimas odds antes do KO
# Enquanto não existirem, estas funções devolvem status="a_recolher".
# Ver _CLV_SETUP no fim do ficheiro para os 2 passos de captura.

_CLV_SIDES = {
    "1X2":     [("H", "home"), ("D", "draw"), ("A", "away")],
    "Over2.5": [("OVER", "o25")],
    "BTTS":    [("YES", "btts")],
}

def _clv_pct(bet_odds, close_odds):
    """CLV% = quanto bateste a linha de fecho. Positivo = apanhaste melhor odd."""
    try:
        return (float(bet_odds) / float(close_odds) - 1.0) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None

def clv_stats(records, since=None):
    """
    CLV médio e % de apostas que bateram o fecho. CLV positivo consistente é
    o melhor preditor de rentabilidade a longo prazo — mais fiável que o ROI,
    que tem muito mais variância em amostras pequenas.
    """
    recs = _filter(records, since)
    samples = []  # (market, clv_pct, beat)
    for r in recs:
        for market, picked_key in [("1X2", "pick_1x2"),
                                    ("Over2.5", "pick_o25"),
                                    ("BTTS", "pick_btts")]:
            if not r.get(picked_key):
                continue
            # lado relevante
            if market == "1X2":
                side_map = {"H": "home", "D": "draw", "A": "away"}
                suff = side_map.get(r.get("pred"))
            else:
                suff = "o25" if market == "Over2.5" else "btts"
            if not suff:
                continue
            bet = r.get(f"bet_pin_{suff}")
            clo = r.get(f"close_pin_{suff}")
            clv = _clv_pct(bet, clo)
            if clv is not None:
                samples.append((market, clv, clv > 0))

    if not samples:
        return {"status": "a_recolher", "n": 0,
                "msg": "Sem odds de aposta+fecho ainda. Ver _CLV_SETUP."}

    def _agg(rows):
        n = len(rows)
        avg = round(sum(c for _, c, _ in rows) / n, 2)
        beat = round(sum(1 for _, _, b in rows if b) / n * 100, 1)
        return {"n": n, "avg_clv_pct": avg, "beat_close_pct": beat}

    by_market = {}
    for m in ("1X2", "Over2.5", "BTTS"):
        rows = [s for s in samples if s[0] == m]
        if rows:
            by_market[m] = _agg(rows)
    return {"status": "ok", **_agg(samples), "by_market": by_market}

# ═══════════════════════════════════════════════════════════════════════════
# PONTO 4 — AMOSTRA IN-SAMPLE vs OUT-OF-SAMPLE
# ═══════════════════════════════════════════════════════════════════════════

def _filter(records, since):
    if not since:
        return records
    return [r for r in records if (r.get("date") or "") >= since]

def sample_split(records):
    """Conta registos antes (in-sample) e desde (OOS) o congelamento."""
    ins = [r for r in records if (r.get("date") or "") < FROZEN_SINCE]
    oos = [r for r in records if (r.get("date") or "") >= FROZEN_SINCE]
    return {"frozen_since": FROZEN_SINCE,
            "in_sample": len(ins), "out_of_sample": len(oos),
            "total": len(records)}

def write_freeze_manifest():
    """Grava os thresholds congelados + data para auditoria/reprodutibilidade."""
    manifest = {
        "frozen_since": FROZEN_SINCE,
        "frozen_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "thresholds": THRESHOLDS,
        "nota": "Tudo com date >= frozen_since e out-of-sample. "
                "Mudar threshold => avancar frozen_since para hoje.",
    }
    try:
        os.makedirs(os.path.dirname(FREEZE_MANIFEST), exist_ok=True)
        with open(FREEZE_MANIFEST, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return manifest

# ═══════════════════════════════════════════════════════════════════════════
# RELATÓRIO
# ═══════════════════════════════════════════════════════════════════════════

def _load_records():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f).get("records", [])
    except (OSError, ValueError):
        return []

def _fmt_roi(b):
    if b["picks"] == 0:
        return "sem picks"
    line = f'{b["picks"]} picks · {b["hits"]} hits · {b["rate"]}% (IC95 {b["ci_lo"]}-{b["ci_hi"]}%)'
    if b["roi_pct"] is not None:
        sign = "+" if b["profit_u"] >= 0 else ""
        line += (f'\n      ROI {sign}{b["roi_pct"]}% · {sign}{b["profit_u"]}u em {b["priced"]} apostas'
                 f' · odds média {b["avg_odds"]} · breakeven {b["breakeven_odds"]}')
        # Veredito segue o ROI REAL (não a odd média vs breakeven — essas podem
        # divergir se acertas mais nas odds baixas e falhas nas altas).
        verdict = "RENTÁVEL ✅" if b["roi_pct"] >= 0 else "A PERDER ❌"
        line += f' → {verdict}'
    else:
        line += '\n      ROI: N/D (sem odds guardadas)'
    return line

def format_report(records, since=None):
    L = []
    scope = f"OUT-OF-SAMPLE (>= {since})" if since else "TODOS os registos (in+out)"
    split = sample_split(records)
    recs  = _filter(records, since)

    L.append("═" * 64)
    L.append(f"  MATEMÁTICA DA BOLA — Análise ROI/CLV/Value · {scope}")
    L.append("═" * 64)
    L.append(f"Congelado em: {split['frozen_since']} | "
             f"in-sample: {split['in_sample']} | "
             f"out-of-sample: {split['out_of_sample']} | total: {split['total']}")
    L.append(f"Registos neste relatório: {len(recs)}")
    if since and len(recs) < 100:
        L.append("⚠️  OOS ainda < 100 registos — nada é conclusivo. Continuar a recolher.")
    L.append("")

    # Ponto 1
    L.append("── PONTO 1 · ROI por mercado (SINGLES) ──────────────────────────")
    mr = market_roi(records, since)
    for m in ("1X2", "Over2.5", "BTTS"):
        L.append(f"  {m:8s} {_fmt_roi(mr[m])}")
    L.append("")

    # Ponto 3
    L.append("── PONTO 3 · Value unificado (edge vs Pinnacle) ─────────────────")
    vr = value_roi(records, since)
    if vr["n"] == 0:
        L.append("  Nenhuma aposta de value no histórico (edge nunca superou threshold).")
    else:
        L.append(f"  {vr['n']} apostas de value · edge médio +{vr['avg_edge_pct']}%")
        L.append(f"  TOTAL   {_fmt_roi(vr['overall'])}")
        for m, b in vr["by_market"].items():
            L.append(f"  {m:8s} {_fmt_roi(b)}")
    L.append("")

    # Ponto 2
    L.append("── PONTO 2 · CLV (Closing Line Value) ───────────────────────────")
    cv = clv_stats(records, since)
    if cv["status"] != "ok":
        L.append(f"  {cv['msg']}")
    else:
        L.append(f"  {cv['n']} apostas · CLV médio {cv['avg_clv_pct']:+}% · "
                 f"bateram o fecho {cv['beat_close_pct']}%")
        for m, b in cv.get("by_market", {}).items():
            L.append(f"  {m:8s} CLV {b['avg_clv_pct']:+}% · beat {b['beat_close_pct']}% (n={b['n']})")
    L.append("")
    L.append("═" * 64)
    L.append("Leitura: 'A PERDER ❌' = acertas muito mas as odds não cobrem. "
             "CLV+ consistente vale mais que ROI+ em amostra pequena.")
    L.append("═" * 64)
    return "\n".join(L)

def main():
    since = FROZEN_SINCE if "--oos" in sys.argv else None
    records = _load_records()
    if not records:
        print(f"Sem registos em {HISTORY_FILE}. Define MDB_HISTORY ou corre na raiz do repo.")
        return
    write_freeze_manifest()
    print(format_report(records, since))

if __name__ == "__main__":
    main()

# ═══════════════════════════════════════════════════════════════════════════
# _CLV_SETUP — os 2 passos para activar o Ponto 2 (CLV)
# ═══════════════════════════════════════════════════════════════════════════
# O CLV precisa de odds em DOIS momentos. Passos mínimos:
#
# (A) No make_record() do backtest.py, guardar 2 conjuntos de odds em vez de 1:
#       "bet_pin_home":  ... (odds no snapshot mais próximo de -30min)
#       "close_pin_home":... (odds do último snapshot antes do KO)
#     ...e o mesmo para draw/away/o25/btts. Por agora podes copiar as odds
#     actuais para ambos (bet==close => CLV 0) e ir refinando.
#
# (B) Garantir um snapshot perto do KO. O teu cron corre 07/14/21 UTC; a
#     janela europeia fecha ~19-21 UTC. Adicionar UMA run extra (ex: cron
#     '45 18 * * *' e '45 20 * * *') que só faz fetch das odds Pinnacle dos
#     jogos a <60min do KO e actualiza close_pin_*. É a única alteração de
#     workflow necessária — o resto deste módulo já está pronto.
