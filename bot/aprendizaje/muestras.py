"""Muestras de operaciones con sus características, para probar hipótesis.

- simuladas: operaciones que la estrategia técnica habría hecho sobre velas históricas (o recientes), con las
  mismas reglas de riesgo del paper trading.
- paper: operaciones reales del paper trading (las únicas con datos de Claude, como su confianza).
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.backtest.motor import ParametrosBacktest, simular
from bot.db.modelos import Operacion, Senal

COLUMNAS = ["par", "direccion", "ts_entrada", "hora_utc", "dia_semana", "vol_rel", "rsi", "atr_pct",
            "confianza_claude", "r_multiple", "pnl_neto"]


def _caracteristicas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ts_entrada"] = pd.to_datetime(df["ts_entrada"], utc=True)
    df["hora_utc"] = df["ts_entrada"].dt.hour
    df["dia_semana"] = df["ts_entrada"].dt.dayofweek
    if "confianza_claude" not in df:
        df["confianza_claude"] = float("nan")
    return df[COLUMNAS]


def muestras_simuladas(senales_por_par: dict[str, pd.DataFrame], pb: ParametrosBacktest, desde=None, hasta=None) -> pd.DataFrame:
    r = simular(senales_por_par, pb, desde, hasta)
    ops = r.operaciones
    if ops.empty:
        return pd.DataFrame(columns=COLUMNAS)
    filas = []
    for o in ops.itertuples():
        s = senales_por_par[o.par].loc[o.ts_senal]
        filas.append({"par": o.par, "direccion": o.direccion, "ts_entrada": o.ts_entrada, "vol_rel": s["vol_rel"],
                      "rsi": s["rsi"], "atr_pct": s["atr"] / s["close"] * 100, "r_multiple": o.r_multiple,
                      "pnl_neto": o.pnl_neto})
    return _caracteristicas(pd.DataFrame(filas))


def muestras_paper(sesion: Session, cartera: str | None = None, desde_ms: int | None = None) -> pd.DataFrame:
    q = select(Operacion, Senal).join(Senal, Senal.id == Operacion.senal_id, isouter=True).where(Operacion.estado == "cerrada")
    if cartera:
        q = q.where(Operacion.cartera == cartera)
    if desde_ms is not None:
        q = q.where(Operacion.ts_entrada_ms >= desde_ms)
    filas = []
    for op, s in sesion.execute(q).all():
        filas.append({
            "par": op.par, "direccion": op.direccion, "ts_entrada": pd.Timestamp(op.ts_entrada_ms, unit="ms", tz="UTC"),
            "vol_rel": s.vol_rel if s else None, "rsi": s.rsi if s else None,
            "atr_pct": (s.atr / s.precio * 100) if s and s.atr and s.precio else None,
            "confianza_claude": op.confianza_claude, "r_multiple": op.r_multiple, "pnl_neto": op.pnl_neto,
        })
    if not filas:
        return pd.DataFrame(columns=COLUMNAS)
    return _caracteristicas(pd.DataFrame(filas))
