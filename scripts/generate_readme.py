"""
Gera as secções dinâmicas do README.md com dados reais de docs/history.json.
Corre: python scripts/generate_readme.py
Actualiza os blocos <!-- STATS_START --> ... <!-- STATS_END --> no README.md.
"""
import json, os, re
from datetime import datetime, timezone

HISTORY_FILE = "docs/history.json"
README_FILE  = "README.md"


def load_history():
    if not os.path.exists(HISTORY_FILE):
        print(f"[WARN] {HISTORY_FILE} não encontrado — README não actualizado")
        return None
    with open(HISTORY_FILE, encoding="utf-8") as f:
        return json.load(f)


def hit_rate(records, pick_key, hit_key):
    picks = [r for r in records if r.get(pick_key)]
    if not picks:
        return 0.0, 0, 0
    hits = sum(1 for r in picks if r.get(hit_key))
    return round(hits / len(picks) * 100, 1), hits, len(picks)


def treble_stats(hist):
    # Tenta ler trebles.json para stats de triplas
    trebles_file = "docs/trebles.json"
    if not os.path.exists(trebles_file):
        return 0, 0
    try:
        t = json.load(open(trebles_file, encoding="utf-8"))
        history = t.get("history", [])
        scored = [x for x in history if x.get("status") == "scored"]
        wins = sum(1 for x in scored if x.get("hit"))
        return wins, len(scored)
    except Exception:
        return 0, 0


def date_range(records):
    dates = [r.get("date", "") for r in records if r.get("date")]
    if not dates:
        return "N/D", "N/D"
    return min(dates), max(dates)


def build_badges(records, treble_wins, treble_total, date_max):
    total    = len(records)
    r1, h1, n1 = hit_rate(records, "pick_1x2",  "hit_1x2")
    r2, h2, n2 = hit_rate(records, "pick_btts", "hit_btts")
    r3, h3, n3 = hit_rate(records, "pick_o25",  "hit_o25")

    treble_str = f"{treble_wins}%2F{treble_total}" if treble_total else "N%2FD"
    updated    = date_max.replace("-", "--")
    color_1x2  = "A855F7"
    color_btts = "00CCFF"
    color_o25  = "F59E0B"
    color_treb = "FF6B6B"

    badges = (
        f"![Records](https://img.shields.io/badge/Registos-{total}-00FF88?style=flat-square&labelColor=0d1117)&nbsp;"
        f"![1X2](https://img.shields.io/badge/1X2_Pick-{r1}%25-{color_1x2}?style=flat-square&labelColor=0d1117)&nbsp;"
        f"![BTTS](https://img.shields.io/badge/BTTS_Pick-{r2}%25-{color_btts}?style=flat-square&labelColor=0d1117)&nbsp;"
        f"![O25](https://img.shields.io/badge/Over_2.5-{r3}%25-{color_o25}?style=flat-square&labelColor=0d1117)&nbsp;"
        f"![Trebles](https://img.shields.io/badge/Triplas-{treble_str}-{color_treb}?style=flat-square&labelColor=0d1117)&nbsp;"
        f"![Updated](https://img.shields.io/badge/Updated-{updated}-555555?style=flat-square&labelColor=0d1117)"
    )
    return badges, total, r1, n1, r2, n2, r3, n3


def build_perf_table(r1, h1, n1, r2, h2, n2, r3, h3, n3, treble_wins, treble_total):
    treble_rate = round(treble_wins / treble_total * 100, 1) if treble_total else 0.0
    return f"""| Mercado | Picks | Hits | Hit Rate | Critério de selecção |
|---------|------:|-----:|---------:|---------------------|
| 1X2 | {n1} | {h1} | **{r1}%** | `best ≥ 61% AND conf == MÉDIA` |
| BTTS | {n2} | {h2} | **{r2}%** | `prob_btts ≥ 61% AND conf ∈ {{ALTA, MÉDIA}}` |
| Over 2.5 | {n3} | {h3} | **{r3}%** | `xg_total ≥ 2.9 AND conf ∈ {{ALTA, MÉDIA}}` |
| Triplas | {treble_total} | {treble_wins} | **{treble_rate}%** | 3 picks BTTS/1X2, máx 1 por liga |"""


def update_readme(badges, perf_table, sample_note):
    if not os.path.exists(README_FILE):
        print(f"[WARN] {README_FILE} não encontrado")
        return

    with open(README_FILE, encoding="utf-8") as f:
        content = f.read()

    # Substitui ambos os blocos STATS_START/STATS_END (há 2 no README)
    new_block = f"<!-- STATS_START -->\n{badges}\n<!-- STATS_END -->"
    content = re.sub(
        r"<!-- STATS_START -->.*?<!-- STATS_END -->",
        new_block,
        content,
        flags=re.DOTALL,
    )

    # Substitui a tabela de performance (entre o parágrafo > Dados actualizados e ### Value Detection)
    perf_pattern = r"(> Dados actualizados automaticamente.*?\n\n<!-- STATS_START -->.*?<!-- STATS_END -->\n\n)\|.*?\| Triplas.*?\|"
    new_perf = rf"\g<1>{perf_table}"
    content_new = re.sub(perf_pattern, new_perf, content, flags=re.DOTALL)

    # Se o regex da tabela não apanhou nada (estrutura diferente), actualiza só os badges
    if content_new == content:
        pass  # badges já foram actualizados acima
    else:
        content = content_new

    # Actualiza a nota de amostra se existir
    if sample_note:
        content = re.sub(
            r"> Dados actualizados automaticamente pelo CI a cada run\. Amostra: .*?\.",
            f"> Dados actualizados automaticamente pelo CI a cada run. {sample_note}.",
            content,
        )

    with open(README_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[OK] {README_FILE} actualizado")


def main():
    hist = load_history()
    if hist is None:
        return

    records = hist.get("records", [])
    if not records:
        print("[WARN] Sem registos em history.json")
        return

    date_min, date_max = date_range(records)
    treble_wins, treble_total = treble_stats(hist)

    badges, total, r1, n1, r2, n2, r3, n3 = build_badges(
        records, treble_wins, treble_total, date_max
    )

    # Conta dias únicos
    days = len(set(r.get("date", "") for r in records if r.get("date")))
    sample_note = f"Amostra: {total} registos, {days} dias"

    perf_table = build_perf_table(r1, h1=n1, n1=n1, r2=r2, h2=n2, n2=n2, r3=r3, h3=n3, n3=n3,
                                   treble_wins=treble_wins, treble_total=treble_total)

    # Recalcular hits reais
    def hits(recs, pk, hk):
        p = [r for r in recs if r.get(pk)]
        return sum(1 for r in p if r.get(hk)), len(p)

    h1r, n1r = hits(records, "pick_1x2",  "hit_1x2")
    h2r, n2r = hits(records, "pick_btts", "hit_btts")
    h3r, n3r = hits(records, "pick_o25",  "hit_o25")

    perf_table = build_perf_table(r1, h1r, n1r, r2, h2r, n2r, r3, h3r, n3r,
                                   treble_wins, treble_total)

    update_readme(badges, perf_table, sample_note)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[OK] generate_readme.py concluído — {ts}")
    print(f"     Registos: {total} | 1X2: {r1}% | BTTS: {r2}% | O25: {r3}% | Triplas: {treble_wins}/{treble_total}")


if __name__ == "__main__":
    main()
