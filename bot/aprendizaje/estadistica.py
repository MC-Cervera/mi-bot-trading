"""Prueba estadística de una hipótesis: ¿el grupo rinde de verdad distinto que el control, o es suerte?

Se compara el R medio (ganancia en múltiplos del riesgo) del grupo contra el del control con una prueba de
permutación (no necesita supuestos de normalidad ni librerías extra). Para pasar se exige TODO:
  1. muestra mínima en grupo y control (por defecto 30 operaciones cada uno),
  2. diferencia en la dirección esperada y de tamaño útil (>= 0.10 R),
  3. significancia: p < 0.05 (menos de 5% de probabilidad de verlo por puro azar),
  4. robustez: la diferencia tiene el mismo signo en la primera y en la segunda mitad del periodo.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

EFECTO_MINIMO_R = 0.10
ALFA = 0.05
PERMUTACIONES = 5000


def p_valor_permutacion(grupo: np.ndarray, control: np.ndarray, esperado: str, semilla: int = 12345) -> float:
    """p-valor de una cola: probabilidad de una diferencia igual o más extrema si no hubiera efecto real."""
    observada = grupo.mean() - control.mean()
    todos = np.concatenate([grupo, control])
    n = len(grupo)
    rng = np.random.default_rng(semilla)
    extremos = 0
    for _ in range(PERMUTACIONES):
        rng.shuffle(todos)
        d = todos[:n].mean() - todos[n:].mean()
        extremos += d >= observada if esperado == "mejor" else d <= observada
    return float((extremos + 1) / (PERMUTACIONES + 1))


@dataclass
class ResultadoPrueba:
    pasa: bool
    suficiente: bool                # hay muestra suficiente para decidir
    n_grupo: int
    n_control: int
    r_medio_grupo: float
    r_medio_control: float
    acierto_grupo_pct: float
    acierto_control_pct: float
    diferencia_r: float
    p_valor: float | None
    robusta: bool | None
    desde: str
    hasta: str
    explicacion: str

    def a_dict(self) -> dict:
        return asdict(self)


def _media(x: pd.Series) -> float:
    return float(x.mean()) if len(x) else 0.0


def comparar(grupo: pd.DataFrame, control: pd.DataFrame, esperado: str, muestra_minima: int,
             nombre_grupo: str = "grupo", nombre_control: str = "control") -> ResultadoPrueba:
    """`grupo` y `control`: DataFrames de operaciones con columnas r_multiple y ts_entrada (datetime)."""
    ng, nc = len(grupo), len(control)
    rg, rc = _media(grupo["r_multiple"]), _media(control["r_multiple"])
    ag = float((grupo["r_multiple"] > 0).mean() * 100) if ng else 0.0
    ac = float((control["r_multiple"] > 0).mean() * 100) if nc else 0.0
    fechas = pd.concat([grupo["ts_entrada"], control["ts_entrada"]]) if ng + nc else pd.Series(dtype="datetime64[ns, UTC]")
    desde = str(fechas.min())[:10] if len(fechas) else "—"
    hasta = str(fechas.max())[:10] if len(fechas) else "—"
    diff = rg - rc
    base = dict(n_grupo=ng, n_control=nc, r_medio_grupo=rg, r_medio_control=rc, acierto_grupo_pct=ag,
                acierto_control_pct=ac, diferencia_r=diff, desde=desde, hasta=hasta)
    if ng < muestra_minima or nc < muestra_minima:
        return ResultadoPrueba(pasa=False, suficiente=False, p_valor=None, robusta=None, **base, explicacion=(
            f"Muestra insuficiente: {ng} operaciones en {nombre_grupo} y {nc} en {nombre_control} "
            f"(hacen falta {muestra_minima} en cada uno). Aún no se puede decidir."))
    signo = 1 if esperado == "mejor" else -1
    p = p_valor_permutacion(grupo["r_multiple"].to_numpy(float), control["r_multiple"].to_numpy(float), esperado)
    corte = fechas.sort_values().iloc[len(fechas) // 2]
    mitades = []
    for sel in (lambda d: d["ts_entrada"] < corte, lambda d: d["ts_entrada"] >= corte):
        g, c = grupo[sel(grupo)], control[sel(control)]
        mitades.append(_media(g["r_multiple"]) - _media(c["r_multiple"]) if len(g) and len(c) else 0.0)
    robusta = bool(all(m * signo > 0 for m in mitades))
    efecto_ok = bool(diff * signo >= EFECTO_MINIMO_R)
    pasa = bool(efecto_ok and p < ALFA and robusta)
    razones = []
    if not efecto_ok:
        razones.append(f"la diferencia ({diff:+.2f}R) no va en la dirección esperada o es menor de {EFECTO_MINIMO_R}R")
    if p >= ALFA:
        razones.append(f"podría ser casualidad (p = {p:.3f}, se exige < {ALFA})")
    if not robusta:
        razones.append(f"no se repite en las dos mitades del periodo ({mitades[0]:+.2f}R y {mitades[1]:+.2f}R)")
    explicacion = (
        f"{nombre_grupo.capitalize()}: {ng} operaciones, acierto {ag:.0f}%, R medio {rg:+.2f}. "
        f"{nombre_control.capitalize()}: {nc} operaciones, acierto {ac:.0f}%, R medio {rc:+.2f}. "
        f"Diferencia {diff:+.2f}R, p = {p:.3f}. "
        + ("PASA: el efecto es grande, poco probable por azar y se repite en ambas mitades."
           if pasa else "NO PASA: " + "; ".join(razones) + ".")
    )
    return ResultadoPrueba(pasa=pasa, suficiente=True, p_valor=p, robusta=robusta, **base, explicacion=explicacion)
