import dataclasses

import numpy as np
import pandas as pd
import pytest

from bot.db.modelos import Senal
from bot.senales import (
    CORTO, LARGO, NINGUNA, ParametrosSenal, entrada_en_horario, extraer_candidatas, generar_senales, guardar_senales,
)
from tests.test_indicadores import a_ms, velas_aleatorias

P = ParametrosSenal()


def velas_seno(n=400, desfase_horas=0, volumen_en=()):
    """Precio sinusoidal (periodo 80h): produce cruces de EMA regulares con RSI en zona media."""
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC") + pd.Timedelta(hours=desfase_horas)
    c = 100 + 5 * np.sin(np.arange(n) * 2 * np.pi / 80)
    vol = np.full(n, 100.0)
    df = pd.DataFrame({"ts": a_ms(idx), "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": vol}, index=idx)
    for ts in volumen_en:
        df.loc[ts, "volume"] = 300.0
    return df


def cruces(df, direccion):
    s = generar_senales(df, P)
    return s.index[s["cruce"] == direccion]


def test_cruce_sin_volumen_se_descarta_con_motivo():
    s = generar_senales(velas_seno(), P)
    c = s[s["cruce"] != NINGUNA]
    assert len(c) > 0
    assert (c["senal"] == NINGUNA).all()
    assert c["motivo"].str.contains("volumen").all()


def test_largo_confirmado_con_volumen():
    base = velas_seno()
    t = cruces(base, LARGO)[0]
    s = generar_senales(velas_seno(volumen_en=[t]), P)
    fila = s.loc[t]
    assert fila["senal"] == LARGO
    assert fila["motivo"] == "confirmada"
    assert fila["sl"] < fila["close"] < fila["tp"]
    assert fila["tp"] - fila["close"] == pytest.approx(P.ratio_tp * (fila["close"] - fila["sl"]))
    assert fila["close"] - fila["sl"] == pytest.approx(P.atr_mult_sl * fila["atr"])


def test_corto_confirmado_con_volumen():
    t = cruces(velas_seno(), CORTO)[0]
    fila = generar_senales(velas_seno(volumen_en=[t]), P).loc[t]
    assert fila["senal"] == CORTO
    assert fila["tp"] < fila["close"] < fila["sl"]


def test_rsi_fuera_de_zona_descarta():
    t = cruces(velas_seno(), LARGO)[0]
    p = dataclasses.replace(P, rsi_compra_min=95, rsi_sobrecompra=99)
    fila = generar_senales(velas_seno(volumen_en=[t]), p).loc[t]
    assert fila["senal"] == NINGUNA
    assert "RSI" in fila["motivo"]


def test_zona_rsi_corto_es_simetrica():
    assert P.zona_rsi_largo == (40, 70)
    assert P.zona_rsi_corto == (30, 60)


def test_horario_limite_antes_del_cierre():
    """Cierre 23:00 UTC y mínimo 2h: la entrada a las 21:00 vale, a las 21:01/22:00/23:00 no; 00:00 sí."""
    horas = pd.DatetimeIndex(pd.to_datetime(
        ["2024-01-01 00:00", "2024-01-01 21:00", "2024-01-01 21:01", "2024-01-01 22:00", "2024-01-01 23:00"], utc=True))
    assert entrada_en_horario(horas, P).tolist() == [True, True, False, False, False]


def test_senal_cerca_del_cierre_se_descarta():
    # desplazamos para que el primer cruce alcista caiga en la vela de 21:00 (entrada 22:00)
    t0 = cruces(velas_seno(), LARGO)[0]
    desfase = (21 - t0.hour) % 24
    base = velas_seno(desfase_horas=desfase)
    t = cruces(base, LARGO)[0]
    assert t.hour == 21
    fila = generar_senales(velas_seno(desfase_horas=desfase, volumen_en=[t]), P).loc[t]
    assert fila["senal"] == NINGUNA
    assert "cierre diario" in fila["motivo"]


def test_senales_son_causales():
    """Una señal en la vela t no depende de velas posteriores (sin sesgo de anticipación)."""
    df = velas_aleatorias(600, semilla=7)
    completo = generar_senales(df, P)
    for corte in (200, 400, 599):
        parcial = generar_senales(df.iloc[:corte], P)
        cols = ["cruce", "senal", "sl", "tp", "motivo"]
        pd.testing.assert_frame_equal(parcial[cols], completo[cols].iloc[:corte])


def test_sin_senales_durante_calentamiento():
    s = generar_senales(velas_aleatorias(300, semilla=3), P)
    assert (s["senal"].iloc[: P.indicadores.calentamiento] == NINGUNA).all()


def test_desde_config(config):
    p = ParametrosSenal.desde_config(config)
    assert p.indicadores.ema_rapida == 9 and p.indicadores.ema_lenta == 21
    assert p.multiplicador_volumen == 1.5
    assert p.hora_cierre_utc == "23:00"


def test_candidata_explica_en_espanol():
    t = cruces(velas_seno(), LARGO)[0]
    s = generar_senales(velas_seno(volumen_en=[t]), P)
    [c] = extraer_candidatas(s, "BTC/USDT", P)
    texto = c.explicar()
    assert c.direccion == "largo"
    assert "cruzó por encima" in texto and "volumen" in texto and "stop loss" in texto


def test_guardar_senales_idempotente(sesion):
    t = cruces(velas_seno(), LARGO)[0]
    s = generar_senales(velas_seno(volumen_en=[t]), P)
    n = guardar_senales(sesion, s, "binance", "BTC/USDT", P)
    guardar_senales(sesion, s, "binance", "BTC/USDT", P)
    filas = sesion.query(Senal).all()
    assert len(filas) == n == (s["cruce"] != NINGUNA).sum()
    assert sum(f.estado == "confirmada" for f in filas) == 1
