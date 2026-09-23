import json

import pandas as pd
import pytest

from bot.cartera import GestorCartera
from bot.config import Secretos
from bot.db.modelos import EventoRiesgo
from bot.ejecucion.base import ErrorEjecucion
from bot.ejecucion.exness_mt5 import BrokerExness, comentario, conectar
from bot.notificaciones import Notificador
from bot.riesgo import Propuesta, evaluar_apertura
from tests.mt5_falso import MT5Falso

ID = "tecnico_claude-BTCUSDT-1719835200000"


def broker(**kw):
    return BrokerExness(MT5Falso(**kw))


def test_solo_cuentas_demo():
    with pytest.raises(ErrorEjecucion, match="NO es demo"):
        broker(cuenta_demo=False)


def test_conectar_usa_credenciales_de_env():
    mt5 = MT5Falso()
    conectar(Secretos(exness_login="123", exness_password="x", exness_servidor="Exness-MT5Trial"), mt5)
    assert mt5.init_kwargs == {"login": 123, "password": "x", "server": "Exness-MT5Trial"}


def test_nombres_de_simbolos():
    b = broker()
    assert b.simbolo("BTC/USDT") == "BTCUSD"
    b.sufijo = "m"
    assert b.simbolo("ETH/USDT") == "ETHUSDm"
    b.simbolos = {"BTC/USDT": "BTCUSD.x"}
    assert b.simbolo("BTC/USDT") == "BTCUSD.x"


def test_lotes_redondean_hacia_abajo_y_respetan_minimo():
    b = broker()
    _, info = b.info("BTC/USDT")
    assert b.lotes(info, 0.0379) == pytest.approx(0.03)  # nunca más de lo que permite el riesgo
    with pytest.raises(ErrorEjecucion, match="lote mínimo"):
        b.lotes(info, 0.005)


def test_abrir_envia_stop_y_objetivo_al_servidor_y_no_duplica():
    b = broker()
    ej = b.abrir(ID, "BTC/USDT", "largo", 0.05, 60000.0, 58800.0, 62400.0)
    p = b.mt5.peticiones[0]
    assert p["type"] == b.mt5.ORDER_TYPE_BUY and p["volume"] == pytest.approx(0.05)
    assert p["price"] == 60010.0                                   # compra al ask
    assert p["sl"] == pytest.approx(60010 - 1200) and p["tp"] == pytest.approx(60010 + 2400)
    assert p["magic"] == 20260923 and p["comment"] == comentario(ID) and len(p["comment"]) <= 31
    assert p["type_filling"] == b.mt5.ORDER_FILLING_IOC            # modo admitido por el símbolo
    assert ej.precio == 60010.0 and ej.ordenes["posicion"] in b.mt5.posiciones
    b.abrir(ID, "BTC/USDT", "largo", 0.05, 60000.0, 58800.0, 62400.0)  # reintento tras un fallo
    assert len(b.mt5.peticiones) == 1 and len(b.mt5.posiciones) == 1


def test_corto_vende_al_bid():
    b = broker()
    b.abrir(ID, "BTC/USDT", "corto", 0.05, 60000.0, 61200.0, 57600.0)
    p = b.mt5.peticiones[0]
    assert p["type"] == b.mt5.ORDER_TYPE_SELL and p["price"] == 60000.0 and p["sl"] > p["price"] > p["tp"]


def test_prueba_otro_modo_de_relleno_si_el_primero_se_rechaza():
    b = broker(filling_mode=3, rechazar_relleno={0})  # admite FOK e IOC, pero FOK falla
    b.abrir(ID, "BTC/USDT", "largo", 0.05, 60000.0, 58800.0, 62400.0)
    assert [x["type_filling"] for x in b.mt5.peticiones] == [0, 1]


def test_stop_demasiado_cerca_se_rechaza():
    b = broker()
    with pytest.raises(ErrorEjecucion, match="mínimo de Exness"):
        b.abrir(ID, "ETH/USDT", "largo", 1.0, 3000.0, 2999.5, 3010.0)  # stops_level 100 puntos = 1.0


def test_simbolo_inexistente():
    with pytest.raises(ErrorEjecucion, match="no existe"):
        broker().abrir(ID, "XLM/USDT", "largo", 100, 0.1, 0.09, 0.12)


def test_cerrar_y_costos_reales():
    b = broker()
    b.abrir(ID, "BTC/USDT", "largo", 0.05, 60000.0, 58800.0, 62400.0)
    ej = b.cerrar(ID, "BTC/USDT", "largo", 0.05, 60500.0, "cierre_diario")
    assert b.mt5.posiciones == {} and ej.precio == 60000.0  # vende al bid
    assert ej.comision == pytest.approx(2 * 0.5 * 0.05)       # comisión de entrada + salida


def test_detecta_stop_ejecutado_por_el_servidor():
    b = broker()
    ej = b.abrir(ID, "BTC/USDT", "largo", 0.05, 60000.0, 58800.0, 62400.0)
    op = type("Op", (), {"par": "BTC/USDT", "direccion": "largo", "precio_entrada": ej.precio,
                         "ordenes_exchange": json.dumps(ej.ordenes)})()
    assert b.cierres_en_exchange([op]) == []
    b.mt5.saltar(ej.ordenes["posicion"], "sl")
    [c] = b.cierres_en_exchange([op])
    assert c.motivo == "stop_loss" and c.precio == pytest.approx(58810.0)


def test_integracion_con_la_cartera(sesion, config):
    mt5 = MT5Falso()
    g = GestorCartera(sesion, config, "tecnico_claude", BrokerExness(mt5), Notificador())
    ahora = pd.Timestamp("2024-07-01 10:01", tz="UTC")
    # ETH: 8 USD de riesgo con stop de 60 USD -> ~0.12 ETH, tope de 333 USD -> 0.11 -> 0.1 lotes (mínimo)
    p = Propuesta(cartera=g.nombre, par="ETH/USDT", direccion="largo", precio_entrada=3000.0, stop_loss=2940.0,
                  take_profit=3120.0, origen="senal_tecnica+claude", confianza=0.7)
    v = evaluar_apertura(p, g.estado_riesgo({"ETH/USDT": 3000.0}, ahora), config, ahora)
    op = g.abrir(p, v, ts_senal=ahora.floor("h"), ahora=ahora, diario="d")
    assert op is not None and json.loads(op.ordenes_exchange)["lotes"] == pytest.approx(0.1)
    assert json.loads(op.ordenes_exchange)["simbolo"] == "ETHUSD"
    g.cobrar_funding({"ETH/USDT": 3000.0}, pd.Timestamp("2024-07-01 16:30", tz="UTC"))
    assert op.funding == 0.0  # Exness cobra el swap real: no se simula funding encima
    mt5.saltar(json.loads(op.ordenes_exchange)["posicion"], "tp")
    g.vigilar({"ETH/USDT": 3130.0}, {}, ahora + pd.Timedelta(hours=3))
    assert op.estado == "cerrada" and op.motivo_salida == "take_profit" and op.pnl_neto > 0


def test_error_de_exness_queda_registrado(sesion, config):
    g = GestorCartera(sesion, config, "tecnico_claude", BrokerExness(MT5Falso()), Notificador())
    ahora = pd.Timestamp("2024-07-01 10:01", tz="UTC")
    p = Propuesta(cartera=g.nombre, par="XLM/USDT", direccion="largo", precio_entrada=0.1, stop_loss=0.096,
                  take_profit=0.108, origen="senal_tecnica+claude", confianza=0.7)
    v = evaluar_apertura(p, g.estado_riesgo({"XLM/USDT": 0.1}, ahora), config, ahora)
    assert g.abrir(p, v, ts_senal=ahora.floor("h"), ahora=ahora, diario="d") is None
    ev = sesion.query(EventoRiesgo).filter_by(tipo="error_ejecucion").one()
    assert "no existe" in ev.detalle


@pytest.mark.parametrize("stop", [57600.0, 59300.0])
def test_btc_no_cabe_en_el_lote_minimo(sesion, config, stop):
    """BTC con lote mínimo 0.01 (~600 USD): ni con stop amplio (riesgo) ni con stop cercano (tope de 333 USD por
    posición a 1x) se llega al mínimo. No se abre y se explica."""
    g = GestorCartera(sesion, config, "tecnico_claude", BrokerExness(MT5Falso()), Notificador())
    ahora = pd.Timestamp("2024-07-01 10:01", tz="UTC")
    p = Propuesta(cartera=g.nombre, par="BTC/USDT", direccion="largo", precio_entrada=60000.0, stop_loss=stop,
                  take_profit=60000.0 + 2 * (60000.0 - stop), origen="senal_tecnica+claude", confianza=0.7)
    v = evaluar_apertura(p, g.estado_riesgo({"BTC/USDT": 60000.0}, ahora), config, ahora)
    assert v.permitido                      # la capa de riesgo lo permite...
    assert g.abrir(p, v, ts_senal=ahora.floor("h"), ahora=ahora, diario="d") is None  # ...pero no cabe en un lote
    assert "lote mínimo" in sesion.query(EventoRiesgo).filter_by(tipo="error_ejecucion").one().detalle


def test_script_de_compatibilidad(config):
    import importlib.util
    spec = importlib.util.spec_from_file_location("probar_exness", "scripts/probar_exness.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    b = broker()
    btc = mod.compatibilidad(b, "BTC/USDT", 1000, config)
    eth = mod.compatibilidad(b, "ETH/USDT", 1000, config)
    xlm = mod.compatibilidad(b, "XLM/USDT", 1000, config)
    assert "❌" in btc and "demasiado grande" in btc      # 0.01 BTC ≈ 600 USD > 333 USD por posición
    assert "✅" in eth
    assert "❌" in xlm and "no existe" in xlm
