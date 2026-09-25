"""Descarga, almacenamiento y lectura de velas OHLCV."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import ccxt
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from bot.datos.exchange import simbolo_mercado
from bot.db.modelos import Vela
from bot.db.sesion import filas_por_bloque

log = logging.getLogger(__name__)

COLUMNAS = ["ts", "open", "high", "low", "close", "volume"]
MAX_REINTENTOS = 3
TRAMO_MS = 60 * 86_400_000  # se guarda cada 60 días descargados


def ms_temporalidad(temporalidad: str) -> int:
    unidades = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    return int(temporalidad[:-1]) * unidades[temporalidad[-1]]


def ahora_ms() -> int:
    return int(time.time() * 1000)


def _fetch_con_reintentos(cliente, simbolo: str, temporalidad: str, desde: int, limite: int) -> list:
    espera = 2
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            return cliente.fetch_ohlcv(simbolo, temporalidad, since=desde, limit=limite)
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as e:
            if intento == MAX_REINTENTOS:
                raise
            log.warning("Error de red en %s (intento %d/%d): %s. Reintento en %ds", simbolo, intento, MAX_REINTENTOS, e, espera)
            time.sleep(espera)
            espera *= 2
    return []


def descargar_velas(
    cliente,
    simbolo: str,
    temporalidad: str,
    desde_ms: int,
    hasta_ms: int | None = None,
    limite: int = 1000,
    ahora: int | None = None,
) -> pd.DataFrame:
    """Descarga velas paginando desde `desde_ms`. Descarta la vela aún abierta (evita look-ahead)."""
    tf = ms_temporalidad(temporalidad)
    ahora = ahora if ahora is not None else ahora_ms()
    hasta_ms = hasta_ms if hasta_ms is not None else ahora
    filas: list = []
    cursor = desde_ms
    while cursor < hasta_ms:
        lote = _fetch_con_reintentos(cliente, simbolo, temporalidad, cursor, limite)
        if not lote:
            break
        filas.extend(lote)
        ultimo = lote[-1][0]
        siguiente = ultimo + tf
        if siguiente <= cursor:  # el exchange no avanzó: evitar bucle infinito
            break
        cursor = siguiente

    df = pd.DataFrame(filas, columns=COLUMNAS)
    if df.empty:
        return df
    df = df.drop_duplicates("ts").sort_values("ts")
    df = df[(df["ts"] >= desde_ms) & (df["ts"] < hasta_ms)]
    df = df[df["ts"] + tf <= ahora]  # solo velas cerradas
    return df.astype({"ts": "int64"}).reset_index(drop=True)


def detectar_huecos(df: pd.DataFrame, temporalidad: str) -> list[tuple[int, int]]:
    """Devuelve [(ts_inicio_hueco, ts_fin_hueco)] donde faltan velas consecutivas."""
    if len(df) < 2:
        return []
    tf = ms_temporalidad(temporalidad)
    ts = df["ts"].to_numpy()
    return [(int(a) + tf, int(b) - tf) for a, b in zip(ts[:-1], ts[1:]) if b - a > tf]


def guardar_velas(sesion: Session, df: pd.DataFrame, exchange: str, par: str, temporalidad: str) -> int:
    """Inserta o actualiza velas (upsert). Devuelve cuántas filas se procesaron."""
    if df.empty:
        return 0
    registros = [
        {"exchange": exchange, "par": par, "temporalidad": temporalidad, **{c: fila[c] for c in COLUMNAS}}
        for fila in df.to_dict("records")
    ]
    bloque = filas_por_bloque(len(registros[0]))
    for i in range(0, len(registros), bloque):
        stmt = insert(Vela).values(registros[i : i + bloque])
        stmt = stmt.on_conflict_do_update(
            index_elements=["exchange", "par", "temporalidad", "ts"],
            set_={c: stmt.excluded[c] for c in ["open", "high", "low", "close", "volume"]},
        )
        sesion.execute(stmt)
    sesion.commit()
    return len(registros)


def ultimo_ts(sesion: Session, exchange: str, par: str, temporalidad: str) -> int | None:
    return sesion.scalar(
        select(func.max(Vela.ts)).where(Vela.exchange == exchange, Vela.par == par, Vela.temporalidad == temporalidad)
    )


def cargar_velas(
    sesion: Session, exchange: str, par: str, temporalidad: str, desde_ms: int | None = None, hasta_ms: int | None = None
) -> pd.DataFrame:
    """Lee velas de la base de datos como DataFrame indexado por fecha UTC."""
    q = select(*(getattr(Vela, c) for c in COLUMNAS)).where(
        Vela.exchange == exchange, Vela.par == par, Vela.temporalidad == temporalidad
    )
    if desde_ms is not None:
        q = q.where(Vela.ts >= desde_ms)
    if hasta_ms is not None:
        q = q.where(Vela.ts < hasta_ms)
    df = pd.DataFrame(sesion.execute(q.order_by(Vela.ts)).all(), columns=COLUMNAS)
    df["fecha"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("fecha")


@dataclass
class ResultadoActualizacion:
    par: str
    nuevas: int
    desde_ms: int
    huecos: list[tuple[int, int]]


def actualizar_historico(
    sesion: Session, cliente, exchange: str, par: str, temporalidad: str, tipo_mercado: str, dias: int,
    ahora: int | None = None, progreso=None,
) -> ResultadoActualizacion:
    """Descarga solo lo que falta desde la última vela guardada (o `dias` hacia atrás si no hay nada)."""
    ahora = ahora if ahora is not None else ahora_ms()
    tf = ms_temporalidad(temporalidad)
    ultimo = ultimo_ts(sesion, exchange, par, temporalidad)
    desde = ultimo + tf if ultimo is not None else ahora - dias * 86_400_000
    desde -= desde % tf  # alinear al inicio de vela
    # se descarga y GUARDA por tramos: si algo falla a mitad, lo ya descargado queda en la base de datos y la
    # próxima ejecución continúa desde la última vela guardada
    guardadas = 0
    cursor = desde
    while cursor < ahora:
        hasta = min(cursor + TRAMO_MS, ahora)
        df = descargar_velas(cliente, simbolo_mercado(par, tipo_mercado), temporalidad, cursor, hasta_ms=hasta, ahora=ahora)
        guardadas += guardar_velas(sesion, df, exchange, par, temporalidad)
        if progreso:
            progreso(par, hasta, ahora)
        cursor = hasta
    huecos = detectar_huecos(cargar_velas(sesion, exchange, par, temporalidad), temporalidad)
    if huecos:
        log.warning("%s: %d hueco(s) en el histórico (ej. %s)", par, len(huecos), huecos[0])
    log.info("%s: %d velas nuevas", par, guardadas)
    return ResultadoActualizacion(par=par, nuevas=guardadas, desde_ms=desde, huecos=huecos)
