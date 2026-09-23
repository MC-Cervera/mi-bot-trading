"""Cada regla de riesgo tiene al menos una prueba que demuestra que BLOQUEA una operación indebida."""
import dataclasses

import pandas as pd
import pytest

from bot.riesgo import (
    ACTIVO, DETENIDO, PAUSADO, EstadoRiesgo, Propuesta, evaluar_apertura, transicion_por_limites, umbral_confianza,
)

AHORA = pd.Timestamp("2024-07-01 12:01", tz="UTC")


def estado(**kw):
    base = dict(estado=ACTIVO, motivo="", capital=1000.0, pico=1000.0, capital_inicio_dia=1000.0, pnl_dia=0.0, posiciones={})
    return EstadoRiesgo(**(base | kw))


def propuesta(**kw):
    base = dict(cartera="tecnico_claude", par="BTC/USDT", direccion="largo", precio_entrada=100.0, stop_loss=96.0,
                take_profit=108.0, origen="senal_tecnica+claude", confianza=0.7, tamano_pct=100.0)
    return Propuesta(**(base | kw))


def bloqueado_por(v, codigo):
    return any(b.startswith(codigo + ":") for b in v.bloqueos)


def test_operacion_correcta_se_permite(config):
    v = evaluar_apertura(propuesta(), estado(), config, AHORA)
    assert v.permitido, v.bloqueos
    assert v.tamano.riesgo_usd == pytest.approx(8.0)
    assert {r.split(":")[0] for r in v.reglas_cumplidas} == {f"R{i}" for i in range(1, 11)}


@pytest.mark.parametrize("est", [DETENIDO, PAUSADO])
def test_R1_bot_no_activo(config, est):
    assert bloqueado_por(evaluar_apertura(propuesta(), estado(estado=est, motivo="x"), config, AHORA), "R1")


def test_R2_perdida_diaria(config):
    v = evaluar_apertura(propuesta(), estado(pnl_dia=-30.0), config, AHORA)  # 3% de 1000
    assert bloqueado_por(v, "R2")
    assert evaluar_apertura(propuesta(), estado(pnl_dia=-29.0), config, AHORA).permitido


def test_R3_drawdown(config):
    assert bloqueado_por(evaluar_apertura(propuesta(), estado(capital=850.0, pico=1000.0), config, AHORA), "R3")


@pytest.mark.parametrize("hora,bloquea", [("20:59", False), ("21:00", False), ("21:01", True), ("22:30", True), ("23:30", True)])
def test_R4_horario_antes_del_cierre(config, hora, bloquea):
    v = evaluar_apertura(propuesta(), estado(), config, pd.Timestamp(f"2024-07-01 {hora}", tz="UTC"))
    assert bloqueado_por(v, "R4") == bloquea


def test_R5_maximo_de_posiciones(config):
    e = estado(posiciones={"ETH/USDT": 100.0, "SOL/USDT": 100.0, "XRP/USDT": 100.0})
    assert bloqueado_por(evaluar_apertura(propuesta(), e, config, AHORA), "R5")


def test_R6_posicion_duplicada_en_el_par(config):
    assert bloqueado_por(evaluar_apertura(propuesta(), estado(posiciones={"BTC/USDT": 100.0}), config, AHORA), "R6")


@pytest.mark.parametrize("kw", [
    dict(stop_loss=0.0),                                    # sin stop
    dict(stop_loss=101.0),                                  # stop de largo por encima del precio
    dict(take_profit=99.0),                                 # objetivo de largo por debajo
    dict(direccion="corto"),                                # corto con stop por debajo
])
def test_R7_stop_loss_obligatorio_y_bien_colocado(config, kw):
    v = evaluar_apertura(propuesta(**kw), estado(), config, AHORA)
    assert bloqueado_por(v, "R7") and not v.permitido


@pytest.mark.parametrize("stop,bloquea", [(99.9, True), (99.8, False), (90.0, False), (89.0, True)])
def test_R8_distancia_del_stop(config, stop, bloquea):
    v = evaluar_apertura(propuesta(stop_loss=stop, take_profit=120), estado(), config, AHORA)
    assert bloqueado_por(v, "R8") == bloquea


def test_R9_confianza_minima(config):
    assert umbral_confianza(config) == 0.6
    assert bloqueado_por(evaluar_apertura(propuesta(confianza=0.59), estado(), config, AHORA), "R9")
    assert bloqueado_por(evaluar_apertura(propuesta(confianza=None), estado(), config, AHORA), "R9")
    assert evaluar_apertura(propuesta(confianza=0.6), estado(), config, AHORA).permitido


def test_R9_umbral_aprendido_mas_exigente(config):
    cfg = config.model_copy(deep=True)
    cfg.estrategia.confianza_min.valor = 0.75
    assert bloqueado_por(evaluar_apertura(propuesta(confianza=0.7), estado(), cfg, AHORA), "R9")


def test_R9_incoherencias_de_claude_bloquean(config):
    v = evaluar_apertura(propuesta(problemas_previos=["Claude propuso corto contra una señal largo"]), estado(), config, AHORA)
    assert bloqueado_por(v, "R9") and "contra una señal" in " ".join(v.bloqueos)


def test_R9_no_aplica_a_la_cartera_sin_claude(config):
    v = evaluar_apertura(propuesta(origen="senal_tecnica", confianza=None), estado(), config, AHORA)
    assert v.permitido


def test_R10_claude_solo_puede_reducir_el_riesgo(config):
    assert evaluar_apertura(propuesta(tamano_pct=50), estado(), config, AHORA).tamano.riesgo_usd == pytest.approx(4.0)
    assert evaluar_apertura(propuesta(tamano_pct=300), estado(), config, AHORA).tamano.riesgo_usd == pytest.approx(8.0)


def test_R10_riesgo_nunca_supera_1pct_si_la_cuenta_baja(config):
    v = evaluar_apertura(propuesta(), estado(capital=900.0, pico=950.0, capital_inicio_dia=900.0), config, AHORA)
    assert v.tamano.riesgo_usd == pytest.approx(8.0)
    v = evaluar_apertura(propuesta(), estado(capital=700.0, pico=700.0, capital_inicio_dia=700.0), config, AHORA)
    assert v.tamano.riesgo_usd == pytest.approx(7.0)


def test_R10_exposicion_total_limitada_por_apalancamiento_1x(config):
    e = estado(posiciones={"ETH/USDT": 600.0, "SOL/USDT": 397.0})
    v = evaluar_apertura(propuesta(), e, config, AHORA)
    assert bloqueado_por(v, "R10")  # solo quedan 3 USD de exposición, por debajo del mínimo de 5


def test_R10_nocional_maximo_reduce_el_riesgo_con_stops_cercanos(config):
    """Stop al 2%: 8 USD exigirían ~372 USD de posición; el tope es capital/3 = 333 USD, así que se arriesga menos."""
    v = evaluar_apertura(propuesta(stop_loss=98.0, take_profit=104.0), estado(), config, AHORA)
    assert v.permitido
    assert v.tamano.nocional == pytest.approx(1000 / 3)
    assert v.tamano.riesgo_usd < 8.0 and v.tamano.limitado_por == "nocional_maximo"


def test_R10_tamano_cero_bloquea(config):
    assert bloqueado_por(evaluar_apertura(propuesta(tamano_pct=0), estado(), config, AHORA), "R10")


def test_se_informan_todos_los_bloqueos_a_la_vez(config):
    v = evaluar_apertura(propuesta(confianza=0.1, stop_loss=101), estado(estado=PAUSADO, motivo="x"), config, AHORA)
    codigos = {b.split(":")[0] for b in v.bloqueos}
    assert {"R1", "R7", "R9", "R10"} <= codigos


def test_transiciones_por_limites(config):
    assert transicion_por_limites(estado(), config) is None
    assert transicion_por_limites(estado(pnl_dia=-30.0), config)[0] == PAUSADO
    assert transicion_por_limites(estado(capital=849.0), config)[0] == DETENIDO
    assert transicion_por_limites(estado(estado=DETENIDO, capital=800.0), config) is None
