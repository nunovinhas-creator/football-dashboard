"""
Schema pandera para validar um registo de make_record() antes de ser
escrito no history.json. Nunca lança excepção — apenas regista warning.
"""

import sys
import pandera as pa
import pandas as pd

# ── Schema ────────────────────────────────────────────────────────────────
RECORD_SCHEMA = pa.DataFrameSchema(
    columns={
        # Identificadores
        "event_id":    pa.Column(int,   nullable=False),
        "date":        pa.Column(str,   pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$")),
        "league":      pa.Column(str,   nullable=False),
        "home":        pa.Column(str,   nullable=False),
        "away":        pa.Column(str,   nullable=False),

        # Resultados reais
        "hs":          pa.Column(int,   pa.Check.ge(0)),
        "as":          pa.Column(int,   pa.Check.ge(0)),
        "goals":       pa.Column(int,   pa.Check.ge(0)),

        # Probabilidades ML (escala 0–100)
        "ph":          pa.Column(float, pa.Check.in_range(0, 100), coerce=True),
        "pd":          pa.Column(float, pa.Check.in_range(0, 100), coerce=True),
        "pa":          pa.Column(float, pa.Check.in_range(0, 100), coerce=True),
        "po":          pa.Column(float, pa.Check.in_range(0, 100), coerce=True),
        "pb":          pa.Column(float, pa.Check.in_range(0, 100), coerce=True),

        # xG e golos previstos
        "xg":           pa.Column(float, pa.Check.ge(0), coerce=True),
        "pred_goals":   pa.Column(float, pa.Check.ge(0), coerce=True),
        "o25_combined": pa.Column(float, pa.Check.in_range(0, 1), coerce=True),

        # Categóricos
        "conf":             pa.Column(str, pa.Check.isin(["ALTA", "MÉDIA", "BAIXA"])),
        "pred":             pa.Column(str, pa.Check.isin(["H", "D", "A"])),
        "real":             pa.Column(str, pa.Check.isin(["H", "D", "A"])),
        "pred_goals_range": pa.Column(str, nullable=False),

        # Picks e hits (booleanos)
        "pick_1x2":       pa.Column(bool),
        "pick_o25":       pa.Column(bool),
        "pick_btts":      pa.Column(bool),
        "pick_goals":     pa.Column(bool),
        "pick_xg":        pa.Column(bool),
        "hit_1x2":        pa.Column(bool),
        "hit_o25":        pa.Column(bool),
        "hit_btts":       pa.Column(bool),
        "hit_goals_o25":  pa.Column(bool),
        "hit_goal_range": pa.Column(bool),

        # Odds Pinnacle (nullable — nem sempre disponíveis)
        "pin_home": pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
        "pin_draw": pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
        "pin_away": pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
        "pin_o25":  pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
        "pin_btts": pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),

        # Campos CLV (nullable — adicionados em Jun 2026)
        "bet_pin_home":   pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
        "bet_pin_draw":   pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
        "bet_pin_away":   pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
        "bet_pin_o25":    pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
        "bet_pin_btts":   pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
        "close_pin_home": pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
        "close_pin_draw": pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
        "close_pin_away": pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
        "close_pin_o25":  pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
        "close_pin_btts": pa.Column(float, pa.Check.in_range(1.01, 50.0), nullable=True, coerce=True),
    },
    strict=False,  # ignora campos extra desconhecidos
    coerce=True,   # coerce tipos compatíveis (int→float)
)


def validate_record(rec: dict) -> bool:
    """
    Valida um registo de make_record() contra o schema.
    Devolve True se válido, False se inválido.
    NUNCA lança excepção — regista warning no stderr e continua.
    O pipeline não deve parar por causa de um registo com dados suspeitos.
    """
    try:
        df = pd.DataFrame([rec])
        RECORD_SCHEMA.validate(df, lazy=True)
        return True
    except pa.errors.SchemaErrors as e:
        # lazy=True agrega todos os erros numa só excepção
        failures = e.failure_cases[["column", "failure_case"]].to_dict("records")
        label = f"{rec.get('date','?')} {rec.get('home','?')} vs {rec.get('away','?')}"
        print(f"[SCHEMA WARN] {label}: {failures}", file=sys.stderr)
        return False
    except pa.errors.SchemaError as e:
        label = f"{rec.get('date','?')} {rec.get('home','?')} vs {rec.get('away','?')}"
        print(f"[SCHEMA WARN] {label}: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[SCHEMA WARN] erro inesperado na validação: {e}", file=sys.stderr)
        return False
