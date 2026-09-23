import numpy as np
import pandas as pd
import pytest

from bot.indicadores import ParametrosIndicadores, atr, calcular_indicadores, ema, rsi, volumen_relativo

# Ejemplos de referencia publicados por StockCharts (ChartSchool).
CIERRES_RSI = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
               46.28, 46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18,
               44.22, 44.57, 43.42, 42.66, 43.13]
RSI_REFERENCIA = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99,
                  41.46, 41.87, 45.46, 37.30, 33.08, 37.77]
CIERRES_EMA = [22.27, 22.19, 22.08, 22.17, 22.18, 22.13, 22.23, 22.43, 22.24, 22.29, 22.15, 22.39, 22.38, 22.61,
               23.36, 24.05, 23.75, 23.83, 23.95, 23.63]
EMA10_REFERENCIA = [22.22, 22.21, 22.24, 22.27, 22.33, 22.52, 22.80, 22.97, 23.13, 23.28, 23.34]


def a_ms(idx: pd.DatetimeIndex) -> np.ndarray:
    """Milisegundos UTC, independiente de la resolución interna de pandas (ns en 2.x, us en 3.x)."""
    return ((idx - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy()


def velas_aleatorias(n=500, semilla=1):
    rng = np.random.default_rng(semilla)
    cierre = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    apertura = np.concatenate([[100], cierre[:-1]])
    alto = np.maximum(apertura, cierre) * (1 + rng.uniform(0, 0.005, n))
    bajo = np.minimum(apertura, cierre) * (1 - rng.uniform(0, 0.005, n))
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"ts": a_ms(idx), "open": apertura, "high": alto, "low": bajo, "close": cierre,
         "volume": rng.uniform(50, 150, n)},
        index=idx,
    )


def test_ema_coincide_con_referencia():
    r = ema(pd.Series(CIERRES_EMA), 10)
    assert r.iloc[:9].isna().all()
    assert r.iloc[9:].round(2).tolist() == EMA10_REFERENCIA


def test_rsi_coincide_con_referencia():
    """StockCharts redondea valores intermedios; toleramos 0.1 puntos de RSI."""
    r = rsi(pd.Series(CIERRES_RSI), 14)
    assert r.iloc[:14].isna().all()
    assert r.iloc[14:].to_numpy() == pytest.approx(RSI_REFERENCIA, abs=0.1)


def test_rsi_extremos():
    assert rsi(pd.Series(np.arange(1.0, 40.0)), 14).iloc[-1] == 100
    assert rsi(pd.Series(np.arange(40.0, 1.0, -1)), 14).iloc[-1] == 0
    assert rsi(pd.Series(np.full(40, 5.0)), 14).iloc[-1] == 50


def test_rsi_siempre_entre_0_y_100():
    r = rsi(velas_aleatorias()["close"]).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_volumen_relativo_excluye_la_vela_actual():
    v = pd.Series([10.0] * 20 + [30.0])
    vr = volumen_relativo(v, 20)
    assert vr.iloc[:20].isna().all()
    assert vr.iloc[-1] == pytest.approx(3.0)


def test_atr_con_rango_constante():
    n = 40
    cierre = pd.Series(np.full(n, 100.0))
    assert atr(cierre + 1, cierre - 1, cierre, 14).iloc[-1] == pytest.approx(2.0)


def test_indicadores_son_causales():
    """Sin sesgo de anticipación: añadir velas futuras no cambia ningún valor pasado."""
    df = velas_aleatorias(400)
    p = ParametrosIndicadores()
    completo = calcular_indicadores(df, p)
    for corte in (60, 150, 399):
        parcial = calcular_indicadores(df.iloc[:corte], p)
        cols = ["ema_rapida", "ema_lenta", "vol_rel", "rsi", "atr"]
        pd.testing.assert_frame_equal(parcial[cols], completo[cols].iloc[:corte])


def test_calentamiento():
    df = calcular_indicadores(velas_aleatorias(200), ParametrosIndicadores())
    p = ParametrosIndicadores()
    assert df[["ema_rapida", "ema_lenta", "vol_rel", "rsi", "atr"]].iloc[p.calentamiento:].notna().all().all()
    assert df["ema_lenta"].iloc[: p.ema_lenta - 1].isna().all()


def test_ts_de_velas_de_prueba_en_milisegundos():
    df = velas_aleatorias(3)
    assert pd.to_datetime(df["ts"].iloc[0], unit="ms", utc=True) == df.index[0]
