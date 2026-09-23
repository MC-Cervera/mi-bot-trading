"""Métricas de rendimiento de un backtest (o de paper trading)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def max_drawdown_pct(curva: pd.Series) -> float:
    """Mayor caída desde un máximo previo, en %."""
    if curva.empty:
        return 0.0
    pico = curva.cummax()
    return float(((pico - curva) / pico).max() * 100)


def peor_racha(pnl: pd.Series) -> int:
    """Mayor número de operaciones perdedoras consecutivas."""
    peor = actual = 0
    for v in pnl:
        actual = actual + 1 if v <= 0 else 0
        peor = max(peor, actual)
    return peor


def sharpe_diario(curva: pd.Series) -> float:
    """Sharpe anualizado con rendimientos diarios (cripto opera 365 días). Tasa libre de riesgo = 0."""
    diario = curva.resample("1D").last().dropna()
    r = diario.pct_change().dropna()
    if len(r) < 2 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * math.sqrt(365))


def calcular_metricas(operaciones: pd.DataFrame, curva: pd.Series, capital_inicial: float) -> dict:
    n = len(operaciones)
    final = float(curva.iloc[-1]) if len(curva) else capital_inicial
    base = {
        "operaciones": n,
        "capital_inicial": capital_inicial,
        "capital_final": final,
        "retorno_pct": (final / capital_inicial - 1) * 100,
        "max_drawdown_pct": max_drawdown_pct(curva),
        "sharpe": sharpe_diario(curva) if len(curva) else 0.0,
    }
    if n == 0:
        return base | {"tasa_acierto_pct": 0.0, "factor_beneficio": 0.0, "expectativa_usd": 0.0, "expectativa_r": 0.0,
                       "ganancia_media_usd": 0.0, "perdida_media_usd": 0.0, "peor_racha": 0,
                       "comisiones_usd": 0.0, "funding_usd": 0.0, "pnl_neto_usd": 0.0}
    pnl = operaciones["pnl_neto"]
    ganadoras, perdedoras = pnl[pnl > 0], pnl[pnl <= 0]
    perdida_bruta = -perdedoras.sum()
    return base | {
        "tasa_acierto_pct": len(ganadoras) / n * 100,
        "factor_beneficio": float(ganadoras.sum() / perdida_bruta) if perdida_bruta > 0 else float("inf"),
        "expectativa_usd": float(pnl.mean()),
        "expectativa_r": float(operaciones["r_multiple"].mean()),
        "ganancia_media_usd": float(ganadoras.mean()) if len(ganadoras) else 0.0,
        "perdida_media_usd": float(perdedoras.mean()) if len(perdedoras) else 0.0,
        "peor_racha": peor_racha(pnl),
        "comisiones_usd": float(operaciones["comisiones"].sum()),
        "funding_usd": float(operaciones["funding"].sum()),
        "pnl_neto_usd": float(pnl.sum()),
    }


def calidad_sistema(operaciones: pd.DataFrame, min_operaciones: int) -> float:
    """Puntuación para elegir parámetros en entrenamiento (tipo SQN): media(R) / desv(R) x raíz(n), n tope 100.

    Premia resultados consistentes, no un par de golpes de suerte. Devuelve -inf si hay pocas operaciones.
    """
    if len(operaciones) < min_operaciones:
        return -math.inf
    r = operaciones["r_multiple"].to_numpy(float)
    desv = r.std(ddof=1)
    if desv == 0 or np.isnan(desv):
        return -math.inf
    return float(r.mean() / desv * math.sqrt(min(len(r), 100)))


def resumen_por(operaciones: pd.DataFrame, columna: str) -> pd.DataFrame:
    if operaciones.empty:
        return pd.DataFrame()
    g = operaciones.groupby(columna)["pnl_neto"]
    return pd.DataFrame({
        "operaciones": g.size(),
        "tasa_acierto_pct": g.apply(lambda s: (s > 0).mean() * 100),
        "pnl_neto_usd": g.sum(),
    }).sort_values("pnl_neto_usd", ascending=False)
