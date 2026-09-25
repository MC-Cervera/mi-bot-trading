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


def test_guarda_por_tramos_y_conserva_lo_descargado_si_falla(sesion, exchange_falso):
    """Si la descarga se corta a mitad, lo ya descargado queda guardado y la siguiente ejecución continúa."""
    import ccxt
    import pytest

    from bot.datos import historico

    n = 24 * 150  # 150 días: 3 tramos de 60 días
    ex = exchange_falso(INICIO, n)
    original = ex.fetch_ohlcv
    llamadas = {"n": 0}

    def falla_a_mitad(*a, **k):
        llamadas["n"] += 1
        if llamadas["n"] == 5:
            raise ccxt.NetworkError("corte de red")
        return original(*a, **k)

    ex.fetch_ohlcv = falla_a_mitad
    historico.MAX_REINTENTOS, antes = 1, historico.MAX_REINTENTOS
    try:
        with pytest.raises(ccxt.NetworkError):
            actualizar_historico(sesion, ex, "binance", "BTC/USDT", "1h", "future", dias=150, ahora=INICIO + n * H)
    finally:
        historico.MAX_REINTENTOS = antes
    guardadas = len(cargar_velas(sesion, "binance", "BTC/USDT", "1h"))
    assert 0 < guardadas < n
    ex.fetch_ohlcv = original
    r = actualizar_historico(sesion, ex, "binance", "BTC/USDT", "1h", "future", dias=150, ahora=INICIO + n * H)
    assert r.nuevas == n - guardadas
    assert len(cargar_velas(sesion, "binance", "BTC/USDT", "1h")) == n and r.huecos == []


def test_cliente_de_datos_solo_carga_el_mercado_que_usa(config):
    from bot.datos.exchange import crear_cliente_datos
    assert crear_cliente_datos(config).options["fetchMarkets"]["types"] == ["linear"]
    assert crear_cliente_datos(config, "spot").options["fetchMarkets"]["types"] == ["spot"]


def test_diagnostico_en_espanol():
    import ccxt

    from bot.datos.exchange import diagnosticar
    assert "451" in diagnosticar(ccxt.ExchangeNotAvailable("451 Service unavailable from a restricted location"))
    assert "--spot" in diagnosticar(ccxt.ExchangeNotAvailable("451 restricted location"))
    assert "conexión" in diagnosticar(ccxt.NetworkError("getaddrinfo failed"))


def test_datos_spot_si_los_futuros_no_responden(config_dict):
    from bot.config import Config
    from bot.datos.exchange import crear_cliente_datos
    config_dict["exchange"]["datos_spot"] = True
    cfg = Config.model_validate(config_dict)
    assert cfg.exchange.mercado_datos == "spot"
    assert crear_cliente_datos(cfg).options["fetchMarkets"]["types"] == ["spot"]


def test_consola_sin_traceback_y_archivo_con_detalle(tmp_path, capsys):
    import logging

    from bot.logging_setup import configurar_logging
    ruta = tmp_path / "bot.log"
    configurar_logging(ruta)
    try:
        raise ValueError("detalle técnico")
    except ValueError:
        logging.getLogger("prueba").exception("Fallo de conexión")
    for h in logging.getLogger().handlers:
        h.flush()
    salida = capsys.readouterr().err
    assert "Fallo de conexión" in salida and "Traceback" not in salida
    assert "Traceback" in ruta.read_text(encoding="utf-8")


def test_diagnostico_ssl():
    import ccxt

    from bot.datos.exchange import diagnosticar
    import ssl
    try:
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate in certificate chain")
        except ssl.SSLError as e:
            raise ccxt.NetworkError("binance GET https://fapi.binance.com/fapi/v1/exchangeInfo") from e
    except ccxt.NetworkError as e:
        texto = diagnosticar(e)
    assert "antivirus" in texto and "Causa: SSLCertVerificationError" in texto
