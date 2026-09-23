import pandas as pd
import pytest

from bot.cartera import GestorCartera
from bot.db.modelos import EventoRiesgo, Operacion, PuntoCapital
from bot.ejecucion.simulado import BrokerSimulado
from bot.notificaciones import Notificador
from bot.riesgo import ACTIVO, DETENIDO, PAUSADO, Propuesta, evaluar_apertura

T = pd.Timestamp("2024-07-01 10:01", tz="UTC")


@pytest.fixture
def g(sesion, config):
    return GestorCartera(sesion, config, "tecnico_solo", BrokerSimulado(0.05, 0.05), Notificador())


@pytest.fixture
def g0(sesion, config):
    """Sin costos, para comprobar cuentas exactas."""
    return GestorCartera(sesion, config, "tecnico_solo", BrokerSimulado(0, 0), Notificador())


def abrir(g, par="BTC/USDT", direccion="largo", precio=100.0, dist=4.0, ahora=T, ts_senal=None, precios=None):
    d = 1 if direccion == "largo" else -1
    p = Propuesta(cartera=g.nombre, par=par, direccion=direccion, precio_entrada=precio, stop_loss=precio - d * dist,
                  take_profit=precio + d * 2 * dist, origen="senal_tecnica")
    v = evaluar_apertura(p, g.estado_riesgo(precios or {par: precio}, ahora), g.config, ahora)
    return g.abrir(p, v, ts_senal=ts_senal or ahora.floor("h") - pd.Timedelta(hours=1), ahora=ahora, diario="diario")


def test_abrir_registra_trazabilidad_y_no_duplica(g, sesion):
    op = abrir(g)
    assert op.estado == "abierta" and op.sugerida_por == "senal_tecnica"
    assert op.id_cliente == "tecnico_solo-BTCUSDT-" + str(int((T.floor("h") - pd.Timedelta(hours=1)).timestamp() * 1000))
    assert "R10" in op.reglas_que_permitieron
    assert op.precio_entrada == pytest.approx(100.05)  # deslizamiento en contra
    assert op.stop_loss == pytest.approx(96.05)        # la distancia al stop se mantiene desde la entrada real
    assert abrir(g) is None                             # misma señal: no se duplica
    assert sesion.query(Operacion).count() == 1
    assert g.avisos.enviados and "LARGO BTC/USDT" in g.avisos.enviados[0]


def test_bloqueo_se_registra_con_motivo(g, sesion):
    for par in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        abrir(g, par=par, precios={"BTC/USDT": 100, "ETH/USDT": 100, "SOL/USDT": 100, "XRP/USDT": 100})
    assert abrir(g, par="XRP/USDT", precios={"BTC/USDT": 100, "ETH/USDT": 100, "SOL/USDT": 100, "XRP/USDT": 100}) is None
    ev = sesion.query(EventoRiesgo).filter_by(tipo="bloqueo").one()
    assert ev.par == "XRP/USDT" and "R5" in ev.detalle


def test_stop_loss_cuesta_el_riesgo_planificado(g, sesion):
    op = abrir(g)
    g.vigilar({"BTC/USDT": 97.0}, {"BTC/USDT": (100.5, 95.0)}, T + pd.Timedelta(hours=1))
    assert op.estado == "cerrada" and op.motivo_salida == "stop_loss"
    assert op.pnl_neto == pytest.approx(-op.riesgo_usd, rel=0.01)
    assert op.pnl_neto == pytest.approx(-8.0, abs=0.05)
    assert "Análisis posterior" in op.analisis_post and "saltó el stop loss" in op.diario_salida


def test_take_profit(g0):
    op = abrir(g0)
    g0.vigilar({"BTC/USDT": 107.0}, {"BTC/USDT": (108.5, 99.0)}, T + pd.Timedelta(hours=2))
    assert op.motivo_salida == "take_profit" and op.precio_salida == 108.0
    assert op.pnl_neto == pytest.approx(8.0 * op.cantidad)          # 2 x distancia al stop (4) x cantidad
    assert op.r_multiple == pytest.approx(op.pnl_neto / op.riesgo_usd)


def test_stop_y_objetivo_en_el_mismo_intervalo_se_asume_stop(g0):
    op = abrir(g0)
    g0.vigilar({"BTC/USDT": 100.0}, {"BTC/USDT": (110.0, 90.0)}, T + pd.Timedelta(hours=1))
    assert op.motivo_salida == "stop_loss"


def test_hueco_ejecuta_al_precio_peor(g0):
    op = abrir(g0)
    g0.vigilar({"BTC/USDT": 90.0}, {}, T + pd.Timedelta(minutes=5))
    assert op.precio_salida == 90.0 and op.pnl_neto < -8


def test_corto(g0):
    op = abrir(g0, direccion="corto")
    g0.vigilar({"BTC/USDT": 92.0}, {}, T + pd.Timedelta(hours=1))
    assert op.motivo_salida == "take_profit" and op.pnl_neto == pytest.approx(8.0 * op.cantidad)


def test_max_favorable_y_adverso(g0):
    op = abrir(g0)
    g0.vigilar({"BTC/USDT": 101.0}, {"BTC/USDT": (106.0, 99.0)}, T + pd.Timedelta(hours=1))
    g0.vigilar({"BTC/USDT": 95.0}, {}, T + pd.Timedelta(hours=2))
    assert op.max_favorable == 106.0 and op.max_adverso == 95.0
    assert "Estuvo al menos 1R a favor y terminó en pérdida" in op.analisis_post


def test_funding_a_las_08_y_16(g0):
    op = abrir(g0, ahora=pd.Timestamp("2024-07-01 07:01", tz="UTC"))
    g0.cobrar_funding({"BTC/USDT": 100.0}, pd.Timestamp("2024-07-01 08:01", tz="UTC"))
    g0.cobrar_funding({"BTC/USDT": 100.0}, pd.Timestamp("2024-07-01 08:01", tz="UTC"))  # no se cobra dos veces
    assert op.funding == pytest.approx(op.cantidad * 100 * 0.0001)
    g0.cobrar_funding({"BTC/USDT": 100.0}, pd.Timestamp("2024-07-01 16:30", tz="UTC"))
    assert op.funding == pytest.approx(2 * op.cantidad * 100 * 0.0001)


def test_pausa_diaria_y_reanudacion_al_dia_siguiente(g0, sesion):
    for i, par in enumerate(("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT")):
        ahora = T + pd.Timedelta(minutes=i * 10)
        abrir(g0, par=par, ahora=ahora, precios={par: 100.0})
        g0.vigilar({par: 90.0}, {}, ahora)
    g0.aplicar_limites({}, T + pd.Timedelta(hours=1))
    assert g0.registro().estado == PAUSADO
    assert abrir(g0, par="ADA/USDT", ahora=T + pd.Timedelta(hours=2), precios={"ADA/USDT": 100.0}) is None
    manana = T + pd.Timedelta(days=1)
    assert g0.estado_riesgo({}, manana).estado == ACTIVO
    assert abrir(g0, par="ADA/USDT", ahora=manana, precios={"ADA/USDT": 100.0}) is not None


def test_drawdown_detiene_cierra_todo_y_avisa(g0, sesion):
    g0.registro().pico = 1200.0  # caída de 1000 vs 1200 = 16.7%
    sesion.commit()
    abrir(g0.__class__(sesion, g0.config, "otra", g0.broker, g0.avisos))  # otra cartera no se ve afectada
    g0.aplicar_limites({"BTC/USDT": 100.0}, T)
    assert g0.registro().estado == DETENIDO
    assert any("🛑" in m for m in g0.avisos.enviados)


def test_emergencia_y_reactivacion(g0, sesion):
    abrir(g0)
    g0.emergencia({"BTC/USDT": 101.0}, T + pd.Timedelta(minutes=30))
    assert g0.abiertas() == [] and g0.registro().estado == DETENIDO
    assert sesion.query(Operacion).one().motivo_salida == "emergencia"
    assert abrir(g0, par="ETH/USDT", ahora=T + pd.Timedelta(hours=1), precios={"ETH/USDT": 100}) is None
    g0.reactivar(T + pd.Timedelta(hours=2), quien="prueba")
    assert g0.registro().estado == ACTIVO


def test_foto_capital(g0, sesion):
    abrir(g0)
    g0.foto_capital({"BTC/USDT": 102.0}, T + pd.Timedelta(hours=1))
    p = sesion.query(PuntoCapital).one()
    assert p.capital == pytest.approx(1000 + 2 * g0.abiertas()[0].cantidad) and p.posiciones_abiertas == 1
