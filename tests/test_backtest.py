import dataclasses

import numpy as np
import pandas as pd
import pytest

from bot.backtest.buy_hold import comprar_y_mantener
from bot.backtest.metricas import calcular_metricas, calidad_sistema, max_drawdown_pct, peor_racha
from bot.backtest.motor import ParametrosBacktest, simular
from bot.backtest.walkforward import generar_ventanas, metricas_fuera_de_muestra, walk_forward
from bot.dimensionamiento import calcular_tamano, riesgo_permitido_usd
from bot.senales import CORTO, LARGO, ParametrosSenal
from tests.test_indicadores import velas_aleatorias

T0 = pd.Timestamp("2024-01-01 00:00", tz="UTC")
SIN_COSTOS = ParametrosBacktest(comision_pct=0, slippage_pct=0, funding_pct_8h=0)
PB = ParametrosBacktest()


def serie(n=30, precio=100.0, inicio=T0):
    """Velas planas (o=h=l=c=precio) sin señales; los tests modifican filas concretas."""
    idx = pd.date_range(inicio, periods=n, freq="h")
    return pd.DataFrame({"open": precio, "high": precio, "low": precio, "close": precio, "senal": 0,
                         "sl": np.nan, "tp": np.nan, "vol_rel": 1.0}, index=idx)


def senal(df, hora, direccion, dist_sl=5.0, ratio=2.0):
    ts = df.index[hora]
    c = df.at[ts, "close"]
    df.loc[ts, ["senal", "sl", "tp"]] = [direccion, c - direccion * dist_sl, c + direccion * ratio * dist_sl]


def vela(df, hora, o=None, h=None, l=None, c=None):
    ts = df.index[hora]
    for col, v in (("open", o), ("high", h), ("low", l), ("close", c)):
        if v is not None:
            df.at[ts, col] = v


# ---------- dimensionamiento ----------

def test_tamano_hace_que_el_stop_cueste_el_riesgo():
    t = calcular_tamano(100, 5, 8, comision_pct=0.05, slippage_pct=0.05, nocional_max=1000, nocional_min=5)
    assert t.riesgo_usd == pytest.approx(8)
    assert t.cantidad == pytest.approx(8 / (5 + 100 * 0.0015))
    assert t.limitado_por == "riesgo"


def test_tamano_limitado_por_nocional_maximo():
    t = calcular_tamano(100, 0.5, 8, 0.05, 0.05, nocional_max=333, nocional_min=5)
    assert t.nocional == pytest.approx(333)
    assert t.riesgo_usd < 8 and t.limitado_por == "nocional_maximo"


def test_tamano_inviable():
    assert calcular_tamano(100, 5, 8, 0, 0, nocional_max=4, nocional_min=5) is None
    assert calcular_tamano(100, 0, 8, 0, 0, 1000, 5) is None


def test_riesgo_baja_si_la_cuenta_baja():
    assert riesgo_permitido_usd(1000, 8, 1) == 8
    assert riesgo_permitido_usd(700, 8, 1) == pytest.approx(7)


# ---------- motor ----------

def test_entrada_en_la_vela_siguiente_y_take_profit():
    df = serie()
    senal(df, 2, LARGO)                 # señal al cierre de las 02:00
    vela(df, 3, o=101, h=101, l=101, c=101)   # se entra a la apertura de las 03:00 (101), no a 100
    vela(df, 5, h=112)                  # TP = 101 + 10 = 111
    r = simular({"BTC": df}, SIN_COSTOS)
    [op] = r.operaciones.to_dict("records")
    assert op["ts_senal"] == df.index[2] and op["ts_entrada"] == df.index[3]
    assert op["precio_entrada"] == 101 and op["stop"] == 96 and op["take_profit"] == 111
    assert op["motivo_salida"] == "take_profit"
    assert op["pnl_neto"] == pytest.approx(8 * 2)  # 2R sin costos
    assert op["r_multiple"] == pytest.approx(2)


def test_perdida_en_stop_es_el_riesgo_fijo_con_costos():
    df = serie()
    senal(df, 2, LARGO)
    vela(df, 6, l=90)
    r = simular({"BTC": df}, PB)
    [op] = r.operaciones.to_dict("records")
    assert op["motivo_salida"] == "stop_loss"
    assert op["pnl_neto"] == pytest.approx(-8, abs=0.02)  # 8 USD incluyendo comisiones y deslizamiento


def test_stop_y_tp_en_la_misma_vela_se_asume_stop():
    df = serie()
    senal(df, 2, LARGO)
    vela(df, 4, h=120, l=80)
    r = simular({"BTC": df}, SIN_COSTOS)
    assert r.operaciones["motivo_salida"].iloc[0] == "stop_loss"


def test_hueco_mas_alla_del_stop_sale_a_la_apertura():
    df = serie()
    senal(df, 2, LARGO)
    vela(df, 4, o=90, h=90, l=89, c=90)  # stop en 95, abre en 90
    r = simular({"BTC": df}, SIN_COSTOS)
    op = r.operaciones.iloc[0]
    assert op["precio_salida"] == 90
    assert op["pnl_neto"] < -8  # el hueco puede costar más que el riesgo planificado


def test_corto_simetrico():
    df = serie()
    senal(df, 2, CORTO)
    vela(df, 5, l=89)
    r = simular({"BTC": df}, SIN_COSTOS)
    op = r.operaciones.iloc[0]
    assert op["direccion"] == "corto" and op["motivo_salida"] == "take_profit"
    assert op["pnl_neto"] == pytest.approx(16)


def test_cierre_diario_a_las_23_utc():
    df = serie(n=30)
    senal(df, 18, LARGO)          # entra a las 19:00
    r = simular({"BTC": df}, SIN_COSTOS)
    op = r.operaciones.iloc[0]
    assert op["motivo_salida"] == "cierre_diario"
    assert op["ts_salida"] == pd.Timestamp("2024-01-01 23:00", tz="UTC")


def test_no_entra_despues_del_cierre_diario():
    df = serie(n=30)
    senal(df, 22, LARGO)          # la entrada sería 23:00
    assert simular({"BTC": df}, SIN_COSTOS).operaciones.empty


def test_maximo_tres_posiciones():
    pares = {}
    for k in range(4):
        df = serie()
        senal(df, 2, LARGO)
        pares[f"P{k}"] = df
    r = simular(pares, SIN_COSTOS)
    assert r.operaciones["ts_entrada"].nunique() == 1 and len(r.operaciones) == 3
    assert [e["tipo"] for e in r.eventos] == ["senal_omitida"]


def test_pausa_por_perdida_diaria():
    """4 pérdidas de 8 USD = 32 USD >= 3% de 1000: la 5.ª señal del día se ignora; al día siguiente se opera."""
    df = serie(n=40)
    for h in (1, 3, 5, 7, 9):
        senal(df, h, LARGO)
        vela(df, h + 1, l=90)
    senal(df, 26, LARGO)  # día siguiente, 02:00
    r = simular({"BTC": df}, ParametrosBacktest(comision_pct=0, slippage_pct=0, funding_pct_8h=0))
    entradas = r.operaciones["ts_entrada"].dt.hour.tolist()
    assert entradas[:4] == [2, 4, 6, 8]
    assert 10 not in entradas
    assert r.operaciones["ts_entrada"].iloc[-1].day == 2
    assert any(e["tipo"] == "pausa_diaria" for e in r.eventos)


def test_parada_por_drawdown():
    """Pérdidas de 8 USD en días distintos hasta caer 15%: el bot se detiene y no vuelve a operar."""
    df = serie(n=24 * 30)
    for d in range(25):
        senal(df, d * 24 + 2, LARGO)
        vela(df, d * 24 + 4, l=90)
    r = simular({"BTC": df}, SIN_COSTOS)
    assert any(e["tipo"] == "parada_drawdown" for e in r.eventos)
    assert len(r.operaciones) < 25
    assert max_drawdown_pct(r.curva) >= 15
    assert max_drawdown_pct(r.curva) < 16


def test_funding_se_cobra_al_cruzar_las_08_utc():
    df = serie()
    senal(df, 5, LARGO)          # entra a las 06:00, sale a las 23:00 por cierre diario
    p = dataclasses.replace(SIN_COSTOS, funding_pct_8h=0.01)
    op = simular({"BTC": df}, p).operaciones.iloc[0]
    assert op["funding"] == pytest.approx(2 * op["nocional"] * 0.0001)  # 08:00 y 16:00


def test_simulacion_sin_mirar_al_futuro():
    """Las operaciones cerradas antes de un corte no cambian si se añaden datos posteriores."""
    from bot.senales import generar_senales
    velas = velas_aleatorias(2000, semilla=11)
    velas["volume"] *= 1 + 2 * (velas.index.hour % 5 == 0)
    s = generar_senales(velas, ParametrosSenal(), con_motivos=False)
    completo = simular({"X": s}, PB).operaciones
    corte = s.index[1200]
    parcial = simular({"X": s.iloc[:1200]}, PB).operaciones
    cerradas = completo[completo["ts_salida"] < corte - pd.Timedelta(hours=1)]
    assert len(cerradas) > 0
    pd.testing.assert_frame_equal(parcial.iloc[: len(cerradas)].reset_index(drop=True), cerradas.reset_index(drop=True))


# ---------- métricas ----------

def test_metricas_basicas():
    ops = pd.DataFrame({"pnl_neto": [10, -5, -5, 20, -5], "r_multiple": [1.25, -0.6, -0.6, 2.5, -0.6],
                        "comisiones": [1] * 5, "funding": [0] * 5})
    curva = pd.Series([1000, 1010, 1005, 1000, 1020, 1015.0],
                      index=pd.date_range("2024-01-01", periods=6, freq="D", tz="UTC"))
    m = calcular_metricas(ops, curva, 1000)
    assert m["tasa_acierto_pct"] == 40
    assert m["factor_beneficio"] == pytest.approx(30 / 15)
    assert m["peor_racha"] == 2
    assert m["retorno_pct"] == pytest.approx(1.5)
    assert m["max_drawdown_pct"] == pytest.approx((1010 - 1000) / 1010 * 100)


def test_peor_racha():
    assert peor_racha(pd.Series([1, -1, -1, -1, 2, -1])) == 3


def test_calidad_exige_minimo_de_operaciones():
    ops = pd.DataFrame({"r_multiple": [2.0, -1.0] * 10})
    assert calidad_sistema(ops, 30) == float("-inf")
    assert calidad_sistema(ops, 20) > 0


def test_comprar_y_mantener():
    idx = pd.date_range("2024-01-01", periods=10, freq="h", tz="UTC")
    a = pd.DataFrame({"open": np.linspace(100, 190, 10), "close": np.linspace(110, 200, 10)}, index=idx)
    b = pd.DataFrame({"open": np.full(10, 50.0), "close": np.full(10, 50.0)}, index=idx)
    r = comprar_y_mantener({"A": a, "B": b}, 1000, idx[0], idx[-1] + pd.Timedelta(hours=1), 0, 0)
    assert r["retorno_pct"] == pytest.approx(50.0)   # A se duplica, B plano -> +50%


# ---------- walk-forward ----------

def test_ventanas_no_se_solapan_en_prueba():
    v = generar_ventanas(pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC"), 6, 2)
    assert len(v) == 9
    for a, b in zip(v, v[1:]):
        assert a.fin_prueba == b.inicio_prueba
    assert all(x.inicio_prueba - x.inicio_entrenamiento >= pd.Timedelta(days=180) for x in v)


def test_walk_forward_de_punta_a_punta(config):
    velas = {p: velas_aleatorias(24 * 300, semilla=s) for p, s in (("A", 1), ("B", 2))}
    for df in velas.values():
        df["volume"] *= 1 + 2 * (df.index.hour % 4 == 0)
    wf = config.backtest.walk_forward.model_copy(update={"entrenamiento_meses": 3, "prueba_meses": 2,
                                                         "min_operaciones_entrenamiento": 10})
    wf.rejilla = wf.rejilla.model_copy(update={"multiplicador_volumen": [1.5, 2.0], "atr_mult_sl": [1.5], "ratio_tp": [2.0]})
    res = walk_forward(velas, ParametrosSenal(), PB, wf)
    assert len(res) >= 2
    for r in res:
        if not r.prueba.operaciones.empty:
            assert (r.prueba.operaciones["ts_entrada"] >= r.ventana.inicio_prueba).all()
            assert (r.prueba.operaciones["ts_salida"] < r.ventana.fin_prueba).all()
        assert r.elegidos.multiplicador_volumen in (1.5, 2.0)
    m = metricas_fuera_de_muestra(res, 1000)
    assert m["capital_inicial"] == 1000 and "sharpe" in m


def test_rejilla_fuera_de_rango_se_rechaza(config_dict):
    from pydantic import ValidationError

    from bot.config import Config
    config_dict["backtest"]["walk_forward"]["rejilla"]["ratio_tp"] = [5.0]
    with pytest.raises(ValidationError, match="ratio_tp"):
        Config.model_validate(config_dict)


def test_parada_por_drawdown_se_mantiene_entre_periodos():
    """Si el bot se detuvo en un periodo, en el siguiente no opera aunque haya señales."""
    df = serie(n=24 * 30)
    for d in range(25):
        senal(df, d * 24 + 2, LARGO)
        vela(df, d * 24 + 4, l=90)
    corte = df.index[24 * 22]
    r1 = simular({"BTC": df}, SIN_COSTOS, hasta=corte)
    assert r1.detenido
    r2 = simular({"BTC": df}, dataclasses.replace(SIN_COSTOS, capital_inicial=r1.capital_final), desde=corte,
                 pico_inicial=r1.pico, detenido_inicial=r1.detenido)
    assert r2.operaciones.empty


def test_drawdown_se_mide_desde_el_pico_anterior():
    """Un periodo que empieza ya en caída cuenta la caída previa."""
    df = serie(n=24 * 5)
    senal(df, 2, LARGO)
    vela(df, 4, l=90)
    r = simular({"BTC": df}, dataclasses.replace(SIN_COSTOS, capital_inicial=855), pico_inicial=1000)
    assert r.detenido  # 855 - 8 = 847: caída de 15.3% desde el pico previo de 1000


def test_sin_pico_previo_la_misma_perdida_no_detiene():
    df = serie(n=24 * 5)
    senal(df, 2, LARGO)
    vela(df, 4, l=90)
    assert not simular({"BTC": df}, dataclasses.replace(SIN_COSTOS, capital_inicial=855)).detenido


def test_conclusiones_son_honestas():
    from bot.backtest.informe import conclusiones
    malo = {"operaciones": 40, "retorno_pct": -5.0, "factor_beneficio": 0.8, "max_drawdown_pct": 9.0}
    bueno = {"operaciones": 40, "retorno_pct": 2.0, "factor_beneficio": 1.2, "max_drawdown_pct": 5.0}
    bh = {"retorno_pct": 30.0, "max_drawdown_pct": 40.0}
    textos = " ".join(conclusiones(malo, bueno, bh, parada=pd.Timestamp("2025-01-01")))
    assert "NO fue rentable" in textos
    assert "sobreajuste" in textos
    assert "DETENIDO" in textos
    assert "muestra es pequeña" in textos
    assert "No superó a comprar y mantener" in textos
