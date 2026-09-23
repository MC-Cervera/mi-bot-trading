"""Referencia "comprar y mantener": repartir el capital a partes iguales entre los pares al inicio y no tocarlo."""
from __future__ import annotations

import pandas as pd

from bot.backtest.metricas import max_drawdown_pct


def comprar_y_mantener(
    velas_por_par: dict[str, pd.DataFrame], capital: float, desde, hasta, comision_pct: float, slippage_pct: float,
) -> dict:
    costos = (comision_pct + slippage_pct) / 100
    valores = []
    for par, df in velas_por_par.items():
        tramo = df[(df.index >= desde) & (df.index < hasta)]
        if tramo.empty:
            continue
        unidades = 1.0 / (tramo["open"].iloc[0] * (1 + costos))  # por cada USD invertido
        valores.append((tramo["close"] * unidades).rename(par))
    if not valores:
        return {"retorno_pct": 0.0, "max_drawdown_pct": 0.0, "pares": 0, "curva": pd.Series(dtype=float)}
    tabla = pd.concat(valores, axis=1).ffill()
    tabla = tabla.dropna(axis=1, how="all").bfill()  # pares que empiezan tarde: se toman desde su primer dato
    curva = tabla.mean(axis=1) * capital * (1 - costos)  # venta final con costos
    return {
        "retorno_pct": (curva.iloc[-1] / capital - 1) * 100,
        "max_drawdown_pct": max_drawdown_pct(curva),
        "pares": tabla.shape[1],
        "curva": curva,
    }
