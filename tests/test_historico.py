from bot.datos.exchange import simbolo_mercado
from bot.datos.historico import (
    actualizar_historico, cargar_velas, descargar_velas, detectar_huecos, guardar_velas, ms_temporalidad,
)

H = 3_600_000
INICIO = 1_700_000_000_000 - (1_700_000_000_000 % H)


def test_ms_temporalidad():
    assert ms_temporalidad("1h") == H
    assert ms_temporalidad("15m") == 15 * 60_000
    assert ms_temporalidad("1d") == 86_400_000


def test_simbolo_mercado():
    assert simbolo_mercado("BTC/USDT", "future") == "BTC/USDT:USDT"
    assert simbolo_mercado("BTC/USDT", "spot") == "BTC/USDT"


def test_descarga_pagina_y_no_duplica(exchange_falso):
    ex = exchange_falso(INICIO, 1200, max_limite=500)
    ahora = INICIO + 1200 * H
    df = descargar_velas(ex, "BTC/USDT:USDT", "1h", INICIO, ahora=ahora)
    assert len(df) == 1200
    assert df["ts"].is_unique and df["ts"].is_monotonic_increasing
    assert ex.llamadas >= 3


def test_descarta_vela_abierta(exchange_falso):
    """La última vela aún no ha cerrado: no debe usarse (evita sesgo de anticipación)."""
    ex = exchange_falso(INICIO, 10)
    ahora = INICIO + 9 * H + H // 2  # a mitad de la vela 9
    df = descargar_velas(ex, "X", "1h", INICIO, ahora=ahora)
    assert len(df) == 9
    assert df["ts"].iloc[-1] == INICIO + 8 * H


def test_detecta_huecos(exchange_falso):
    ex = exchange_falso(INICIO, 10, faltantes={4, 5})
    df = descargar_velas(ex, "X", "1h", INICIO, ahora=INICIO + 10 * H)
    assert detectar_huecos(df, "1h") == [(INICIO + 4 * H, INICIO + 5 * H)]


def test_guardar_es_idempotente(sesion, exchange_falso):
    ex = exchange_falso(INICIO, 50)
    df = descargar_velas(ex, "X", "1h", INICIO, ahora=INICIO + 50 * H)
    guardar_velas(sesion, df, "binance", "BTC/USDT", "1h")
    guardar_velas(sesion, df, "binance", "BTC/USDT", "1h")
    assert len(cargar_velas(sesion, "binance", "BTC/USDT", "1h")) == 50


def test_actualizacion_incremental(sesion, exchange_falso):
    ex = exchange_falso(INICIO, 100)
    r1 = actualizar_historico(sesion, ex, "binance", "BTC/USDT", "1h", "future", dias=2, ahora=INICIO + 48 * H)
    assert r1.nuevas == 48
    r2 = actualizar_historico(sesion, ex, "binance", "BTC/USDT", "1h", "future", dias=2, ahora=INICIO + 60 * H)
    assert r2.nuevas == 12
    assert r2.desde_ms == INICIO + 48 * H
    df = cargar_velas(sesion, "binance", "BTC/USDT", "1h")
    assert len(df) == 60 and r2.huecos == []
    assert str(df.index.tz) == "UTC"
