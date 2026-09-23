"""Indicadores técnicos: EMA, RSI (Wilder), volumen relativo y ATR.

Implementados con numpy/pandas (sin pandas-ta) para no depender de una librería con problemas de
compatibilidad. Todos son CAUSALES: el valor en la vela t usa solo datos hasta t (inclusive).
Durante el calentamiento (datos insuficientes) devuelven NaN.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def ema(serie: pd.Series, periodo: int) -> pd.Series:
    """EMA sembrada con la media simple de las primeras `periodo` velas (como TradingView)."""
    x = serie.to_numpy(dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) < periodo:
        return pd.Series(out, index=serie.index)
    alfa = 2.0 / (periodo + 1)
    out[periodo - 1] = x[:periodo].mean()
    for i in range(periodo, len(x)):
        out[i] = alfa * x[i] + (1 - alfa) * out[i - 1]
    return pd.Series(out, index=serie.index)


def _rma(x: np.ndarray, periodo: int, inicio: int) -> np.ndarray:
    """Media móvil de Wilder desde `inicio`, sembrada con la media simple de `periodo` valores."""
    out = np.full(len(x), np.nan)
    semilla = inicio + periodo - 1
    if len(x) <= semilla:
        return out
    out[semilla] = x[inicio : semilla + 1].mean()
    for i in range(semilla + 1, len(x)):
        out[i] = (out[i - 1] * (periodo - 1) + x[i]) / periodo
    return out


def rsi(cierre: pd.Series, periodo: int = 14) -> pd.Series:
    """RSI de Wilder. 0-100."""
    c = cierre.to_numpy(dtype=float)
    delta = np.diff(c, prepend=np.nan)
    ganancia = np.where(delta > 0, delta, 0.0)
    perdida = np.where(delta < 0, -delta, 0.0)
    g = _rma(ganancia, periodo, inicio=1)
    p = _rma(perdida, periodo, inicio=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        valores = np.where(p == 0, np.where(g == 0, 50.0, 100.0), 100 - 100 / (1 + g / p))
    valores[np.isnan(g)] = np.nan
    return pd.Series(valores, index=cierre.index)


def volumen_relativo(volumen: pd.Series, periodo: int = 20) -> pd.Series:
    """Volumen de la vela / promedio de las `periodo` velas ANTERIORES (la actual no entra en el promedio)."""
    promedio = volumen.shift(1).rolling(periodo, min_periods=periodo).mean()
    return volumen / promedio.replace(0, np.nan)


def atr(alto: pd.Series, bajo: pd.Series, cierre: pd.Series, periodo: int = 14) -> pd.Series:
    """Average True Range de Wilder. Se usa para colocar el stop loss técnico, no como señal."""
    cierre_prev = cierre.shift(1)
    rango = pd.concat([alto - bajo, (alto - cierre_prev).abs(), (bajo - cierre_prev).abs()], axis=1).max(axis=1)
    rango.iloc[0] = np.nan
    return pd.Series(_rma(rango.to_numpy(dtype=float), periodo, inicio=1), index=cierre.index)


@dataclass(frozen=True)
class ParametrosIndicadores:
    ema_rapida: int = 9
    ema_lenta: int = 21
    periodo_volumen: int = 20
    periodo_rsi: int = 14
    periodo_atr: int = 14

    @property
    def calentamiento(self) -> int:
        """Velas mínimas antes de que todos los indicadores tengan valor."""
        return max(self.ema_lenta, self.periodo_volumen + 1, self.periodo_rsi + 1, self.periodo_atr + 1)


def calcular_indicadores(velas: pd.DataFrame, p: ParametrosIndicadores) -> pd.DataFrame:
    """Añade columnas de indicadores a un DataFrame con open/high/low/close/volume."""
    df = velas.copy()
    df["ema_rapida"] = ema(df["close"], p.ema_rapida)
    df["ema_lenta"] = ema(df["close"], p.ema_lenta)
    df["vol_rel"] = volumen_relativo(df["volume"], p.periodo_volumen)
    df["rsi"] = rsi(df["close"], p.periodo_rsi)
    df["atr"] = atr(df["high"], df["low"], df["close"], p.periodo_atr)
    return df
