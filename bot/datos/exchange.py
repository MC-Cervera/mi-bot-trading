"""Conexión al exchange vía ccxt.

Se usan dos clientes separados:
- cliente de DATOS: mercado real, solo endpoints públicos, sin claves. El histórico del
  entorno demo es incompleto, así que las velas siempre vienen del mercado real.
- cliente de CUENTA: con claves, apuntando a Binance Demo Trading mientras `exchange.demo` sea true.
"""
from __future__ import annotations

import logging

import ccxt

from bot.config import Config, ErrorConfiguracion, Secretos

log = logging.getLogger(__name__)


def simbolo_mercado(par: str, tipo_mercado: str) -> str:
    """'BTC/USDT' -> 'BTC/USDT:USDT' en futuros perpetuos lineales; igual en spot."""
    if tipo_mercado == "future":
        return par if ":" in par else f"{par}:{par.split('/')[1]}"
    return par


def _opciones(tipo_mercado: str) -> dict:
    return {
        "enableRateLimit": True,
        "options": {"defaultType": "future" if tipo_mercado == "future" else "spot"},
    }


def crear_cliente_datos(config: Config) -> ccxt.Exchange:
    """Cliente público (sin claves) del mercado real para descargar velas."""
    clase = getattr(ccxt, config.exchange.nombre)
    return clase(_opciones(config.exchange.tipo_mercado))


def crear_cliente_cuenta(config: Config, secretos: Secretos) -> ccxt.Exchange:
    """Cliente autenticado para saldo y (en fases posteriores) órdenes."""
    if not secretos.tiene_claves_exchange:
        raise ErrorConfiguracion("Faltan BINANCE_API_KEY / BINANCE_API_SECRET en .env")
    clase = getattr(ccxt, config.exchange.nombre)
    cliente = clase(
        {**_opciones(config.exchange.tipo_mercado), "apiKey": secretos.binance_api_key, "secret": secretos.binance_api_secret}
    )
    if config.exchange.demo:
        cliente.enable_demo_trading(True)
        log.info("Cliente de cuenta en modo DEMO (demo.binance.com)")
    elif config.modo != "real":
        raise ErrorConfiguracion("exchange.demo: false solo se permite en modo real autorizado.")
    return cliente


def validar_pares(cliente: ccxt.Exchange, pares: list[str], tipo_mercado: str) -> tuple[list[str], list[str]]:
    """Separa los pares configurados en (disponibles, no disponibles) en el exchange."""
    mercados = cliente.load_markets()
    validos, invalidos = [], []
    for par in pares:
        simbolo = simbolo_mercado(par, tipo_mercado)
        m = mercados.get(simbolo)
        (validos if m and m.get("active", True) else invalidos).append(par)
    return validos, invalidos


def leer_saldo(cliente: ccxt.Exchange, moneda: str = "USDT") -> dict:
    saldo = cliente.fetch_balance()
    return {
        "moneda": moneda,
        "total": float(saldo.get("total", {}).get(moneda) or 0.0),
        "libre": float(saldo.get("free", {}).get(moneda) or 0.0),
        "usado": float(saldo.get("used", {}).get(moneda) or 0.0),
    }


def verificar_permisos_claves(cliente: ccxt.Exchange) -> dict:
    """Comprueba que las claves NO permitan retiros.

    Devuelve {"verificable": bool, "retiros_habilitados": bool | None, "detalle": str}.
    Binance solo expone este dato en el mercado real (endpoint sapi); en demo no se puede
    verificar y así se informa, sin suponer nada.
    """
    try:
        r = cliente.sapi_get_account_apirestrictions()
    except Exception as e:  # noqa: BLE001 - cualquier fallo significa "no verificable"
        return {"verificable": False, "retiros_habilitados": None, "detalle": f"No verificable: {type(e).__name__}"}
    retiros = bool(r.get("enableWithdrawals"))
    return {
        "verificable": True,
        "retiros_habilitados": retiros,
        "detalle": "PELIGRO: la clave permite retiros" if retiros else "OK: la clave no permite retiros",
    }
