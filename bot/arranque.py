"""Construye todas las piezas del bot a partir de la configuración (lo usan los scripts)."""
from __future__ import annotations

from bot.aprendizaje.motor import ProveedorLecciones
from bot.cartera import CARTERA_CLAUDE, CARTERA_SOLO, GestorCartera
from bot.ciclo import Ciclo
from bot.config import RAIZ, cargar_config, cargar_secretos, validar_seguridad
from bot.datos.exchange import crear_cliente_cuenta
from bot.db import crear_motor, crear_sesion
from bot.ejecucion.simulado import BrokerSimulado
from bot.ia.cliente import ClienteClaude
from bot.logging_setup import configurar_logging
from bot.mercado import MercadoCcxt
from bot.notificaciones import Notificador

ARCHIVO_DETENER = RAIZ / "data" / "DETENER"


def construir(con_mercado: bool = True):
    config = cargar_config()
    secretos = cargar_secretos()
    validar_seguridad(config, secretos)
    configurar_logging(config.rutas.absoluta(config.rutas.logs))
    sesion = crear_sesion(crear_motor(config.rutas.absoluta(config.rutas.base_datos)))
    avisos = Notificador(secretos.telegram_bot_token, secretos.telegram_chat_id, config.telegram.habilitado)
    simulado = BrokerSimulado(config.backtest.comision_pct, config.backtest.slippage_pct)
    broker_claude = simulado
    if config.ejecucion.broker == "binance_demo":
        from bot.ejecucion.binance_demo import BrokerBinanceDemo
        broker_claude = BrokerBinanceDemo(crear_cliente_cuenta(config, secretos), config.exchange.tipo_mercado)
    elif config.ejecucion.broker == "exness_demo":
        from bot.ejecucion.exness_mt5 import BrokerExness, conectar
        e = config.exness
        broker_claude = BrokerExness(conectar(secretos), e.sufijo_simbolo, e.simbolos, e.magico, e.desviacion_puntos)
    # La cartera de control siempre es simulada: así la comparación usa exactamente los mismos supuestos.
    carteras = {
        CARTERA_CLAUDE: GestorCartera(sesion, config, CARTERA_CLAUDE, broker_claude, avisos),
        CARTERA_SOLO: GestorCartera(sesion, config, CARTERA_SOLO, simulado, avisos),
    }
    claude = ClienteClaude(config.claude, sesion, api_key=secretos.anthropic_api_key) if secretos.anthropic_api_key else None
    mercado = MercadoCcxt(sesion, config) if con_mercado else None
    ciclo = Ciclo(sesion, config, mercado, carteras, claude, avisos, lecciones=ProveedorLecciones(sesion))
    return config, sesion, ciclo
