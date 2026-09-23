"""Condiciones que definen un subgrupo de operaciones ("cruces con volumen >= 2x", "cortos en horario asiático"...).

Toda hipótesis de tipo filtro se expresa con estas condiciones: así el código puede probarla sin interpretar texto.
"""
from __future__ import annotations

import json
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

CAMPOS_TECNICOS = ("par", "direccion", "hora_utc", "dia_semana", "vol_rel", "rsi", "atr_pct")
CAMPOS_SOLO_PAPER = ("confianza_claude",)   # solo existen en operaciones que pasaron por Claude
Campo = Literal["par", "direccion", "hora_utc", "dia_semana", "vol_rel", "rsi", "atr_pct", "confianza_claude"]
NOMBRES = {
    "par": "par", "direccion": "dirección", "hora_utc": "hora de entrada (UTC)", "dia_semana": "día de la semana (0=lunes)",
    "vol_rel": "volumen relativo", "rsi": "RSI", "atr_pct": "volatilidad (ATR en % del precio)",
    "confianza_claude": "confianza de Claude",
}
OPS = {">=": "≥", "<=": "≤", ">": ">", "<": "<", "==": "=", "!=": "≠"}


class Condicion(BaseModel):
    campo: Campo
    op: Literal[">=", "<=", ">", "<", "==", "!="]
    valor: float | str = Field(description="Número, o texto para 'par' (ej. BTC/USDT) y 'direccion' (largo/corto)")

    def mascara(self, df: pd.DataFrame) -> pd.Series:
        col = df[self.campo]
        v = self.valor
        if self.op in ("==", "!="):
            igual = col.astype(str) == str(v) if isinstance(v, str) else col == float(v)
            return igual if self.op == "==" else ~igual
        v = float(v)
        return {">=": col >= v, "<=": col <= v, ">": col > v, "<": col < v}[self.op].fillna(False)

    def describir(self) -> str:
        valor = f"{self.valor:g}" if isinstance(self.valor, float) else self.valor
        return f"{NOMBRES[self.campo]} {OPS[self.op]} {valor}"


def mascara(condiciones: list[Condicion], df: pd.DataFrame) -> pd.Series:
    m = pd.Series(True, index=df.index)
    for c in condiciones:
        m &= c.mascara(df)
    return m


def describir(condiciones: list[Condicion]) -> str:
    return " y ".join(c.describir() for c in condiciones) or "todas las operaciones"


def a_json(condiciones: list[Condicion]) -> str:
    return json.dumps([c.model_dump() for c in condiciones], ensure_ascii=False, sort_keys=True)


def desde_json(texto: str) -> list[Condicion]:
    return [Condicion.model_validate(c) for c in json.loads(texto or "[]")]


def solo_paper(condiciones: list[Condicion]) -> bool:
    return any(c.campo in CAMPOS_SOLO_PAPER for c in condiciones)
