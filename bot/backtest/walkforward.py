"""Validación walk-forward.

Se divide el histórico en ventanas consecutivas:  [ entrenamiento (6 meses) | prueba (2 meses) ] -> avanzar 2 meses.
En cada ventana:
  1. Se prueban todas las combinaciones de la rejilla SOLO con datos de entrenamiento y se elige la mejor
     (con un mínimo de operaciones para no premiar la suerte).
  2. Esa combinación se aplica a la ventana de prueba, que el optimizador nunca vio (fuera de muestra).
  3. En la misma ventana de prueba se corren también los parámetros por defecto, como referencia.
El resultado honesto de la estrategia es la suma de las ventanas de PRUEBA.

Sin sesgo de anticipación: los indicadores son causales y se calculan sobre todo el histórico; en la ventana de
prueba solo se opera con señales de velas dentro de esa ventana.
"""
from __future__ import annotations

import dataclasses
import itertools
import logging
from dataclasses import dataclass

import pandas as pd

from bot.backtest.metricas import calcular_metricas, calidad_sistema
from bot.backtest.motor import ParametrosBacktest, ResultadoBacktest, simular
from bot.indicadores import ParametrosIndicadores, calcular_indicadores
from bot.senales import ParametrosSenal, generar_senales

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Ventana:
    inicio_entrenamiento: pd.Timestamp
    inicio_prueba: pd.Timestamp
    fin_prueba: pd.Timestamp


def generar_ventanas(inicio: pd.Timestamp, fin: pd.Timestamp, meses_entrenamiento: int, meses_prueba: int) -> list[Ventana]:
    ventanas = []
    ini = inicio
    while True:
        ini_prueba = ini + pd.DateOffset(months=meses_entrenamiento)
        fin_prueba = ini_prueba + pd.DateOffset(months=meses_prueba)
        if fin_prueba > fin:
            break
        ventanas.append(Ventana(ini, ini_prueba, fin_prueba))
        ini = ini + pd.DateOffset(months=meses_prueba)
    return ventanas


def combinaciones(base: ParametrosSenal, rejilla) -> list[ParametrosSenal]:
    """Todas las combinaciones de la rejilla aplicadas sobre los parámetros base."""
    return [
        dataclasses.replace(
            base,
            indicadores=dataclasses.replace(base.indicadores, ema_rapida=r, ema_lenta=l),
            multiplicador_volumen=v, atr_mult_sl=a, ratio_tp=t,
        )
        for (r, l), v, a, t in itertools.product(rejilla.ema, rejilla.multiplicador_volumen, rejilla.atr_mult_sl, rejilla.ratio_tp)
    ]


class CacheSenales:
    """Guarda los indicadores (dependen solo de los periodos) y las señales de pocas combinaciones a la vez.

    Guardar las señales de TODAS las combinaciones ocuparía gigabytes, así que solo se retienen las últimas.
    """

    def __init__(self, velas_por_par: dict[str, pd.DataFrame], temporalidad: str, max_senales: int = 4):
        self.velas = velas_por_par
        self.temporalidad = temporalidad
        self.max_senales = max_senales
        self._ind: dict[tuple, dict[str, pd.DataFrame]] = {}
        self._sen: dict[ParametrosSenal, dict[str, pd.DataFrame]] = {}

    def indicadores(self, p: ParametrosIndicadores) -> dict[str, pd.DataFrame]:
        clave = dataclasses.astuple(p)
        if clave not in self._ind:
            self._ind[clave] = {par: calcular_indicadores(df, p) for par, df in self.velas.items()}
        return self._ind[clave]

    def senales(self, p: ParametrosSenal) -> dict[str, pd.DataFrame]:
        if p not in self._sen:
            if len(self._sen) >= self.max_senales:
                self._sen.pop(next(iter(self._sen)))
            ind = self.indicadores(p.indicadores)
            self._sen[p] = {
                par: generar_senales(df, p, self.temporalidad, indicadores=ind[par], con_motivos=False)
                for par, df in self.velas.items()
            }
        return self._sen[p]


@dataclass
class ResultadoVentana:
    ventana: Ventana
    elegidos: ParametrosSenal
    calidad_entrenamiento: float
    operaciones_entrenamiento: int
    prueba: ResultadoBacktest
    prueba_por_defecto: ResultadoBacktest


def walk_forward(
    velas_por_par: dict[str, pd.DataFrame], base: ParametrosSenal, pb: ParametrosBacktest, config_wf,
    temporalidad: str = "1h", progreso=None,
) -> list[ResultadoVentana]:
    cache = CacheSenales(velas_por_par, temporalidad)
    candidatos = combinaciones(base, config_wf.rejilla)
    inicio = min(df.index[0] for df in velas_por_par.values())
    fin = max(df.index[-1] for df in velas_por_par.values())
    # margen de calentamiento: el entrenamiento empieza cuando los indicadores ya tienen valor
    inicio += pd.Timedelta(hours=max(max(e) for e in config_wf.rejilla.ema) * 3)
    ventanas = generar_ventanas(inicio.normalize(), fin, config_wf.entrenamiento_meses, config_wf.prueba_meses)
    if not ventanas:
        return []

    # 1) Entrenamiento: cada combinación se evalúa en todas las ventanas (las señales se generan una vez).
    mejor: list[tuple[float, ParametrosSenal | None, int]] = [(float("-inf"), None, 0)] * len(ventanas)
    for j, p in enumerate(candidatos, 1):
        senales = cache.senales(p)
        for k, v in enumerate(ventanas):
            r = simular(senales, pb, v.inicio_entrenamiento, v.inicio_prueba)
            q = calidad_sistema(r.operaciones, config_wf.min_operaciones_entrenamiento)
            if q > mejor[k][0]:
                mejor[k] = (q, p, len(r.operaciones))
        if progreso:
            progreso("entrenamiento", j, len(candidatos))

    # 2) Prueba fuera de muestra, encadenando el capital como si el bot siguiera operando.
    # El pico de capital y la parada por drawdown también se encadenan: si el bot se detuvo, no vuelve a operar.
    resultados = []
    ant = ant_def = None
    for k, v in enumerate(ventanas):
        calidad, elegido, n = mejor[k]
        if elegido is None:
            log.warning("Ventana %d: ninguna combinación alcanzó %d operaciones; se usan los parámetros por defecto",
                        k + 1, config_wf.min_operaciones_entrenamiento)
            elegido = base
        prueba = _continuar(cache.senales(elegido), pb, v, ant)
        prueba_def = _continuar(cache.senales(base), pb, v, ant_def)
        ant, ant_def = prueba, prueba_def
        resultados.append(ResultadoVentana(v, elegido, calidad, n, prueba, prueba_def))
        if progreso:
            progreso("prueba", k + 1, len(ventanas))
    return resultados


def _continuar(senales, pb: ParametrosBacktest, v: Ventana, anterior: ResultadoBacktest | None) -> ResultadoBacktest:
    if anterior is None:
        return simular(senales, pb, v.inicio_prueba, v.fin_prueba)
    return simular(senales, dataclasses.replace(pb, capital_inicial=anterior.capital_final), v.inicio_prueba,
                   v.fin_prueba, pico_inicial=anterior.pico, detenido_inicial=anterior.detenido)


def unir_pruebas(resultados: list[ResultadoVentana], por_defecto: bool = False) -> tuple[pd.DataFrame, pd.Series]:
    """Concatena operaciones y curvas de capital de todas las ventanas de prueba."""
    rs = [r.prueba_por_defecto if por_defecto else r.prueba for r in resultados]
    con_ops = [r.operaciones for r in rs if not r.operaciones.empty]
    ops = pd.concat(con_ops, ignore_index=True) if con_ops else pd.DataFrame()
    curva = pd.concat([r.curva for r in rs]) if rs else pd.Series(dtype=float)
    return ops, curva


def metricas_fuera_de_muestra(resultados: list[ResultadoVentana], capital_inicial: float, por_defecto=False) -> dict:
    ops, curva = unir_pruebas(resultados, por_defecto)
    return calcular_metricas(ops, curva, capital_inicial)
