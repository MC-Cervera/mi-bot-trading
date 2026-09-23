"""Prueba de punta a punta del ciclo con mercado y Claude simulados: dos carteras, mismas señales."""
import numpy as np
import pandas as pd
import pytest

from bot.cartera import CARTERA_CLAUDE, CARTERA_SOLO, GestorCartera
from bot.ciclo import Ciclo
from bot.datos.historico import guardar_velas
from bot.db.modelos import EventoRiesgo, LlamadaClaude, Operacion, PuntoCapital, Senal
from bot.ejecucion.simulado import BrokerSimulado
from bot.ia.cliente import ClienteClaude
from bot.ia.decision import DecisionClaude
from bot.noticias.analisis import AnalisisNoticia, LoteAnalisis
from bot.noticias.fuentes import NoticiaCruda
from bot.notificaciones import Notificador
from bot.senales import LARGO, ParametrosSenal, generar_senales
from tests.claude_falso import ClienteFalso, respuesta
from tests.test_indicadores import a_ms


class MercadoFalso:
    def __init__(self, precios):
        self._precios = precios

    def actualizar(self, ahora):
        pass

    def precios(self):
        return dict(self._precios)


def preparar(sesion, config):
    """Velas sinusoidales de BTC cuya última vela cerrada es un cruce alcista confirmado por volumen."""
    n = 400
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    c = 100 + 5 * np.sin(np.arange(n) * 2 * np.pi / 80)
    df = pd.DataFrame({"ts": a_ms(idx), "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 100.0}, index=idx)
    s = generar_senales(df, ParametrosSenal.desde_config(config))
    t = s.index[(s["cruce"] == LARGO) & (s.index.hour < 20) & (np.arange(n) > 100)][0]
    df.loc[t, "volume"] = 400.0
    df = df[df.index <= t]
    guardar_velas(sesion, df.reset_index(drop=True), "binance", "BTC/USDT", "1h")
    ahora = t + pd.Timedelta(hours=1, minutes=1)
    return ahora, float(df["close"].iloc[-1])


def decision(**kw):
    base = dict(accion="comprar", par="BTC/USDT", confianza=0.72, tamano_sugerido_pct=100, stop_loss=0.0,
                take_profit=0.0, razonamiento="Cruce con volumen alto", hipotesis_que_aplica=[],
                factores_a_favor=["volumen 4x"], factores_en_contra=[])
    return DecisionClaude(**(base | kw))


def construir(sesion, config, precio, *respuestas_claude, lector=lambda url, ahora: []):
    cfg = config.model_copy(deep=True)
    cfg.pares = ["BTC/USDT"]
    cfg.noticias.fuentes_rss = ["https://ej.com/rss"]
    avisos = Notificador()
    broker = BrokerSimulado(0.05, 0.05)
    carteras = {n: GestorCartera(sesion, cfg, n, broker, avisos) for n in (CARTERA_CLAUDE, CARTERA_SOLO)}
    claude = ClienteClaude(cfg.claude, sesion, cliente=ClienteFalso(*respuestas_claude)) if respuestas_claude else None
    return Ciclo(sesion, cfg, MercadoFalso({"BTC/USDT": precio}), carteras, claude, avisos, lector_rss=lector)


def test_claude_aprueba_ambas_carteras_operan(sesion, config):
    ahora, precio = preparar(sesion, config)
    dist = precio * 0.03
    ciclo = construir(sesion, config, precio, respuesta(decision(stop_loss=precio - dist, take_profit=precio + 2 * dist)))
    ciclo.ciclo_horario(ahora)
    ops = {o.cartera: o for o in sesion.query(Operacion)}
    assert set(ops) == {CARTERA_CLAUDE, CARTERA_SOLO}
    oc = ops[CARTERA_CLAUDE]
    assert oc.sugerida_por == "senal_tecnica+claude" and oc.confianza_claude == 0.72
    assert oc.llamada_claude_id is not None and oc.senal_id is not None
    ll = sesion.get(LlamadaClaude, oc.llamada_claude_id)
    assert ll.senal_id == oc.senal_id == sesion.query(Senal).filter_by(estado="confirmada").one().id
    assert "Qué dijo Claude" in oc.diario_entrada and "Cruce con volumen alto" in oc.diario_entrada
    assert "Sin Claude" in ops[CARTERA_SOLO].diario_entrada
    assert sesion.query(PuntoCapital).count() == 2
    # repetir el ciclo en la misma hora no duplica operaciones ni paga otra consulta
    ciclo.ciclo_horario(ahora)
    assert sesion.query(Operacion).count() == 2 and sesion.query(LlamadaClaude).count() == 1


def test_claude_rechaza_solo_opera_el_control(sesion, config):
    ahora, precio = preparar(sesion, config)
    ciclo = construir(sesion, config, precio, respuesta(decision(accion="mantener", confianza=0.3)))
    ciclo.ciclo_horario(ahora)
    assert [o.cartera for o in sesion.query(Operacion)] == [CARTERA_SOLO]
    ev = sesion.query(EventoRiesgo).filter_by(tipo="rechazada_por_claude").one()
    assert ev.cartera == CARTERA_CLAUDE and "mantener" in ev.detalle


def test_claude_con_confianza_baja_bloqueado_por_riesgo(sesion, config):
    ahora, precio = preparar(sesion, config)
    dist = precio * 0.03
    ciclo = construir(sesion, config, precio,
                      respuesta(decision(confianza=0.5, stop_loss=precio - dist, take_profit=precio + 2 * dist)))
    ciclo.ciclo_horario(ahora)
    assert [o.cartera for o in sesion.query(Operacion)] == [CARTERA_SOLO]
    ev = sesion.query(EventoRiesgo).filter_by(tipo="bloqueo", cartera=CARTERA_CLAUDE).one()
    assert "R9" in ev.detalle and ev.llamada_claude_id is not None


def test_sin_clave_de_claude_la_cartera_claude_no_opera(sesion, config):
    ahora, precio = preparar(sesion, config)
    ciclo = construir(sesion, config, precio)
    ciclo.ciclo_horario(ahora)
    assert [o.cartera for o in sesion.query(Operacion)] == [CARTERA_SOLO]
    assert "Claude no configurado" in sesion.query(EventoRiesgo).filter_by(cartera=CARTERA_CLAUDE).one().detalle


def test_monitor_cierra_todo_a_las_23(sesion, config):
    ahora, precio = preparar(sesion, config)
    ciclo = construir(sesion, config, precio)
    ciclo.ciclo_horario(ahora)
    ciclo.monitor(ahora.normalize() + pd.Timedelta(hours=23))
    op = sesion.query(Operacion).one()
    assert op.estado == "cerrada" and op.motivo_salida == "cierre_diario"


def test_noticia_de_alto_impacto_hace_que_claude_cierre(sesion, config):
    ahora, precio = preparar(sesion, config)
    dist = precio * 0.03
    lector = lambda url, t: [NoticiaCruda("ej.com", "Bitcoin exchange hacked", "", "https://ej.com/h", t - 60_000)]
    noticia_id = {}

    def lote(kwargs):
        from bot.db.modelos import Noticia
        noticia_id["id"] = sesion.query(Noticia).one().id
        return respuesta(LoteAnalisis(noticias=[AnalisisNoticia(
            id=noticia_id["id"], relevante=True, resumen="Hackeo grande", activos=["BTC"], sentimiento=-0.9,
            impacto="alto", horizonte="horas", tema="hackeo_seguridad")]))

    ciclo = construir(sesion, config, precio,
                      respuesta(decision(stop_loss=precio - dist, take_profit=precio + 2 * dist)),  # abrir
                      lote,                                                                          # noticias
                      respuesta(decision(accion="cerrar", stop_loss=0, take_profit=0, razonamiento="Riesgo por hackeo")),
                      lector=lector)
    ciclo.ciclo_horario(ahora)
    ciclo.ciclo_noticias(ahora + pd.Timedelta(minutes=20))
    oc = sesion.query(Operacion).filter_by(cartera=CARTERA_CLAUDE).one()
    assert oc.estado == "cerrada" and oc.motivo_salida == "claude_noticia"
    assert "Bitcoin exchange hacked" in oc.analisis_post
    assert sesion.query(Operacion).filter_by(cartera=CARTERA_SOLO).one().estado == "abierta"  # el control no ve noticias
    assert any("impacto ALTO" in m for m in ciclo.avisos.enviados)
