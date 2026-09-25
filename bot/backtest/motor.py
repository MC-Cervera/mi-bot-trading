"""Motor de backtesting vela a vela, con cartera compartida entre todos los pares.

Supuestos (conservadores a propósito):
- La señal se detecta al CIERRE de la vela t y se entra a la APERTURA de la vela t+1 (nunca antes).
- Entrada y stop se ejecutan a mercado con deslizamiento en contra. El take profit es una orden límite (sin deslizamiento).
- Comisión en cada lado. Funding cobrado siempre como costo a las 00, 08 y 16 UTC si la posición estaba abierta.
- Si en una misma vela se tocan el stop y el take profit, se asume que saltó el STOP (no se conoce el orden real).
- Si el precio abre más allá del stop (hueco), se sale a la apertura, no al precio del stop.
- A la hora de cierre diario (23:00 UTC) se cierra todo a la apertura de esa vela.
- Se omiten las señales con el stop a menos de 0.2% o más de 10% del precio (regla R8, igual que en vivo).
- Reglas de riesgo: pérdida fija por operación (8 USD, o menos si 1% del capital es menor), máximo de posiciones,
  nocional máximo = capital x apalancamiento / máximo de posiciones, pausa diaria por pérdida, parada por drawdown.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from bot.dimensionamiento import calcular_tamano, riesgo_permitido_usd
from bot.senales import LARGO, NINGUNA

HORAS_FUNDING = (0, 8, 16)
ATAJO_SIN_SENALES = True  # (las pruebas lo desactivan para comprobar que el atajo no cambia nada)


@dataclass(frozen=True)
class ParametrosBacktest:
    capital_inicial: float = 1000.0
    sl_usd: float = 8.0
    riesgo_max_pct: float = 1.0
    max_posiciones: int = 3
    apalancamiento: int = 1
    perdida_diaria_max_pct: float = 3.0
    drawdown_max_pct: float = 15.0
    hora_cierre_utc: str = "23:00"
    comision_pct: float = 0.05
    slippage_pct: float = 0.05
    funding_pct_8h: float = 0.01
    nocional_min_usd: float = 5.0
    distancia_stop_min_pct: float = 0.2    # regla R8 de la capa de riesgo en vivo
    distancia_stop_max_pct: float = 10.0

    @classmethod
    def desde_config(cls, config, capital_inicial: float | None = None) -> "ParametrosBacktest":
        r, b = config.riesgo, config.backtest
        return cls(
            capital_inicial=capital_inicial or config.capital.simulado_usd,
            sl_usd=r.sl_usd_por_operacion, riesgo_max_pct=r.riesgo_max_pct_operacion,
            max_posiciones=r.max_posiciones, apalancamiento=r.apalancamiento,
            perdida_diaria_max_pct=r.perdida_diaria_max_pct, drawdown_max_pct=r.drawdown_max_pct,
            hora_cierre_utc=r.hora_cierre_diario_utc, comision_pct=b.comision_pct, slippage_pct=b.slippage_pct,
            funding_pct_8h=b.funding_pct_8h, nocional_min_usd=b.nocional_min_usd,
            distancia_stop_min_pct=config.ejecucion.distancia_stop_min_pct,
            distancia_stop_max_pct=config.ejecucion.distancia_stop_max_pct,
        )

    @property
    def minuto_cierre(self) -> int:
        hh, mm = self.hora_cierre_utc.split(":")
        return int(hh) * 60 + int(mm)


@dataclass
class _Posicion:
    par: str
    direccion: int
    ts_senal: pd.Timestamp
    ts_entrada: pd.Timestamp
    i_entrada: int
    precio_entrada: float
    cantidad: float
    stop: float
    take_profit: float
    riesgo_usd: float
    comisiones: float
    funding: float = 0.0


@dataclass
class Operacion:
    par: str
    direccion: str
    ts_senal: pd.Timestamp
    ts_entrada: pd.Timestamp
    ts_salida: pd.Timestamp
    precio_entrada: float
    precio_salida: float
    cantidad: float
    nocional: float
    stop: float
    take_profit: float
    motivo_salida: str
    pnl_bruto: float
    comisiones: float
    funding: float
    pnl_neto: float
    riesgo_usd: float
    r_multiple: float


@dataclass
class ResultadoBacktest:
    operaciones: pd.DataFrame
    curva: pd.Series                 # capital marcado a mercado al cierre de cada vela
    eventos: list[dict] = field(default_factory=list)
    capital_inicial: float = 0.0
    pico: float = 0.0                # máximo de capital alcanzado (para encadenar periodos)
    detenido: bool = False           # True si se alcanzó el drawdown máximo: el bot queda parado

    @property
    def capital_final(self) -> float:
        return float(self.curva.iloc[-1]) if len(self.curva) else self.capital_inicial


@dataclass
class SenalesAlineadas:
    """Señales de todos los pares en un índice común, como arrays numpy. Se calculan una vez y se recortan por
    ventana sin copiar (el walk-forward simula cientos de ventanas sobre las mismas señales)."""

    indice: pd.DatetimeIndex
    arrays: dict[str, dict[str, np.ndarray]]

    def recortar(self, desde, hasta) -> tuple[pd.DatetimeIndex, dict]:
        a = 0 if desde is None else self.indice.searchsorted(desde, side="left")
        b = len(self.indice) if hasta is None else self.indice.searchsorted(hasta, side="left")
        return self.indice[a:b], {par: {k: v[a:b] for k, v in cols.items()} for par, cols in self.arrays.items()}


def alinear(senales_por_par: dict[str, pd.DataFrame]) -> SenalesAlineadas:
    indice = None
    for df in senales_por_par.values():
        indice = df.index if indice is None else indice.union(df.index)
    indice = indice if indice is not None else pd.DatetimeIndex([], tz="UTC")
    arrays = {}
    for par, df in senales_por_par.items():
        r = df.reindex(indice)
        dist_sl = (r["close"] - r["sl"]).abs()
        dist_tp = (r["tp"] - r["close"]).abs()
        arrays[par] = {
            "o": r["open"].to_numpy(float), "h": r["high"].to_numpy(float), "l": r["low"].to_numpy(float),
            "c": r["close"].to_numpy(float),
            "senal": r["senal"].fillna(NINGUNA).to_numpy(int),
            "dist_sl": dist_sl.to_numpy(float), "dist_tp": dist_tp.to_numpy(float),
            "vol_rel": r["vol_rel"].to_numpy(float),
        }
    return SenalesAlineadas(indice, arrays)


def simular(
    senales_por_par: dict[str, pd.DataFrame] | SenalesAlineadas,
    p: ParametrosBacktest,
    desde: pd.Timestamp | None = None,
    hasta: pd.Timestamp | None = None,
    pico_inicial: float | None = None,
    detenido_inicial: bool = False,
) -> ResultadoBacktest:
    """Simula la estrategia sobre las señales ya calculadas (salida de generar_senales) de varios pares.

    Las señales de la última vela ANTES de `desde` no se operan: el periodo empieza limpio.
    `pico_inicial` / `detenido_inicial` permiten continuar el estado de un periodo anterior: el drawdown se mide
    desde el máximo histórico y, si el bot ya estaba detenido, sigue detenido (solo una persona lo reactiva).
    """
    if not isinstance(senales_por_par, SenalesAlineadas):
        senales_por_par = alinear(senales_por_par)
    indice, A = senales_por_par.recortar(desde, hasta)
    com, slip = p.comision_pct / 100, p.slippage_pct / 100
    efectivo = p.capital_inicial          # capital realizado (incluye PnL cerrado)
    abiertas: dict[str, _Posicion] = {}
    operaciones: list[Operacion] = []
    eventos: list[dict] = []
    curva = np.full(len(indice), np.nan)
    pico = max(p.capital_inicial, pico_inicial or 0.0)
    detenido = detenido_inicial
    dia_actual = None
    capital_inicio_dia = efectivo
    pnl_dia = 0.0
    pausado_dia = False

    def cerrar(pos: _Posicion, precio: float, ts, motivo: str) -> None:
        nonlocal efectivo, pnl_dia
        comision_salida = pos.cantidad * precio * com
        bruto = (precio - pos.precio_entrada) * pos.cantidad * pos.direccion
        neto = bruto - pos.comisiones - comision_salida - pos.funding
        efectivo += neto
        pnl_dia += neto
        operaciones.append(Operacion(
            par=pos.par, direccion="largo" if pos.direccion == LARGO else "corto", ts_senal=pos.ts_senal,
            ts_entrada=pos.ts_entrada, ts_salida=ts, precio_entrada=pos.precio_entrada, precio_salida=precio,
            cantidad=pos.cantidad, nocional=pos.cantidad * pos.precio_entrada, stop=pos.stop,
            take_profit=pos.take_profit, motivo_salida=motivo, pnl_bruto=bruto,
            comisiones=pos.comisiones + comision_salida, funding=pos.funding, pnl_neto=neto,
            riesgo_usd=pos.riesgo_usd, r_multiple=neto / pos.riesgo_usd if pos.riesgo_usd else 0.0,
        ))
        del abiertas[pos.par]

    # Precalculado una vez (crear un Timestamp por vela es lento en 5m/15m).
    n = len(indice)
    dias = indice.as_unit("s").asi8 // 86_400 if n else np.array([], dtype=np.int64)  # día UTC como entero
    minutos = np.asarray(indice.hour * 60 + indice.minute) if n else np.array([], dtype=np.int64)
    # velas donde podría entrarse (alguna señal al cierre de la vela anterior)
    hay_senal = np.zeros(n, dtype=bool)
    for a in A.values():
        hay_senal[1:] |= a["senal"][:-1] != NINGUNA
    con_senal = np.flatnonzero(hay_senal)

    minuto_cierre = p.minuto_cierre
    i = 0
    while i < n:
        if ATAJO_SIN_SENALES and not abiertas and not hay_senal[i]:
            # Sin posiciones ni señales nada cambia: el capital sigue igual hasta la próxima señal (atajo exacto).
            k = np.searchsorted(con_senal, i)
            j = int(con_senal[k]) if k < len(con_senal) else n
            curva[i:j] = efectivo
            i = j
            continue
        # --- cambio de día UTC ---
        if dias[i] != dia_actual:
            dia_actual = dias[i]
            capital_inicio_dia = efectivo
            pnl_dia = 0.0
            pausado_dia = False
        minuto_dia = int(minutos[i])

        # --- cierre diario obligatorio ---
        if minuto_dia >= minuto_cierre:
            for par in list(abiertas):
                a = A[par]
                if not np.isnan(a["o"][i]):
                    pos = abiertas[par]
                    cerrar(pos, a["o"][i] * (1 - slip * pos.direccion), indice[i], "cierre_diario")

        # --- funding (costo) ---
        if minuto_dia % 60 == 0 and minuto_dia // 60 in HORAS_FUNDING and p.funding_pct_8h > 0:
            for pos in abiertas.values():
                a = A[pos.par]
                if pos.i_entrada < i and not np.isnan(a["o"][i]):
                    pos.funding += pos.cantidad * a["o"][i] * p.funding_pct_8h / 100

        # --- entradas: señales confirmadas al cierre de la vela anterior ---
        if i > 0 and not detenido and not pausado_dia and minuto_dia < minuto_cierre:
            candidatos = [
                par for par, a in A.items()
                if a["senal"][i - 1] != NINGUNA and not np.isnan(a["o"][i]) and par not in abiertas
            ]
            if len(candidatos) > 1:  # más volumen primero
                candidatos.sort(key=lambda par: -np.nan_to_num(A[par]["vol_rel"][i - 1]))
            for par in candidatos:
                if len(abiertas) >= p.max_posiciones:
                    eventos.append({"ts": indice[i], "tipo": "senal_omitida", "par": par, "detalle": "máximo de posiciones"})
                    continue
                a = A[par]
                d = int(a["senal"][i - 1])
                entrada = a["o"][i] * (1 + slip * d)
                dist_sl, dist_tp = a["dist_sl"][i - 1], a["dist_tp"][i - 1]
                dist_pct = dist_sl / entrada * 100
                if not p.distancia_stop_min_pct <= dist_pct <= p.distancia_stop_max_pct:
                    eventos.append({"ts": indice[i], "tipo": "senal_omitida", "par": par,
                                    "detalle": f"stop a {dist_pct:.2f}% del precio (R8)"})
                    continue
                riesgo = riesgo_permitido_usd(efectivo, p.sl_usd, p.riesgo_max_pct)
                tam = calcular_tamano(
                    entrada, dist_sl, riesgo, p.comision_pct, p.slippage_pct,
                    nocional_max=efectivo * p.apalancamiento / p.max_posiciones, nocional_min=p.nocional_min_usd,
                )
                if tam is None:
                    eventos.append({"ts": indice[i], "tipo": "senal_omitida", "par": par, "detalle": "tamaño no viable"})
                    continue
                abiertas[par] = _Posicion(
                    par=par, direccion=d, ts_senal=indice[i - 1], ts_entrada=indice[i], i_entrada=i, precio_entrada=entrada,
                    cantidad=tam.cantidad, stop=entrada - d * dist_sl, take_profit=entrada + d * dist_tp,
                    riesgo_usd=tam.riesgo_usd, comisiones=tam.nocional * com,
                )

        # --- salidas por stop / take profit dentro de la vela ---
        for par in list(abiertas):
            pos = abiertas[par]
            a = A[par]
            o, h, l = a["o"][i], a["h"][i], a["l"][i]
            if np.isnan(o):
                continue
            nueva = pos.i_entrada == i
            if pos.direccion == LARGO:
                if not nueva and o <= pos.stop:
                    cerrar(pos, o * (1 - slip), indice[i], "stop_loss")
                elif l <= pos.stop:
                    cerrar(pos, pos.stop * (1 - slip), indice[i], "stop_loss")
                elif not nueva and o >= pos.take_profit:
                    cerrar(pos, o, indice[i], "take_profit")
                elif h >= pos.take_profit:
                    cerrar(pos, pos.take_profit, indice[i], "take_profit")
            else:
                if not nueva and o >= pos.stop:
                    cerrar(pos, o * (1 + slip), indice[i], "stop_loss")
                elif h >= pos.stop:
                    cerrar(pos, pos.stop * (1 + slip), indice[i], "stop_loss")
                elif not nueva and o <= pos.take_profit:
                    cerrar(pos, o, indice[i], "take_profit")
                elif l <= pos.take_profit:
                    cerrar(pos, pos.take_profit, indice[i], "take_profit")

        # --- capital marcado a mercado ---
        no_realizado = 0.0
        for pos in abiertas.values():
            c = A[pos.par]["c"][i]
            if not np.isnan(c):
                no_realizado += (c - pos.precio_entrada) * pos.cantidad * pos.direccion - pos.funding
        capital = efectivo + no_realizado
        curva[i] = capital
        pico = max(pico, capital)

        # --- límites de pérdida ---
        if not pausado_dia and pnl_dia <= -p.perdida_diaria_max_pct / 100 * capital_inicio_dia:
            pausado_dia = True
            eventos.append({"ts": indice[i], "tipo": "pausa_diaria", "par": "", "detalle": f"pérdida del día {pnl_dia:.2f} USD"})
        if not detenido and (pico - capital) / pico * 100 >= p.drawdown_max_pct:
            detenido = True
            for par in list(abiertas):
                cerrar(abiertas[par], A[par]["c"][i], indice[i], "drawdown_maximo")
            curva[i] = efectivo
            eventos.append({"ts": indice[i], "tipo": "parada_drawdown", "par": "",
                            "detalle": f"caída de {(pico - efectivo) / pico * 100:.1f}% desde el máximo"})
        i += 1

    # --- fin de datos: cerrar lo que quede al último cierre disponible ---
    for par in list(abiertas):
        c = A[par]["c"]
        ult = np.flatnonzero(~np.isnan(c))
        cerrar(abiertas[par], c[ult[-1]], indice[ult[-1]], "fin_datos")
    if len(curva):
        curva[-1] = efectivo

    ops = pd.DataFrame([vars(o) for o in operaciones])  # (asdict copia en profundidad: lento)
    return ResultadoBacktest(
        operaciones=ops, curva=pd.Series(curva, index=indice, name="capital"), eventos=eventos,
        capital_inicial=p.capital_inicial, pico=pico, detenido=detenido,
    )
