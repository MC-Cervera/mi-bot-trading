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
    # Solo se cargan los mercados que se usan: por defecto ccxt consulta spot, futuros USDⓈ-M y COIN-M, y si
    # cualquiera de esos servidores falla (o está restringido en tu región) no se puede descargar nada.
    tipos = ["linear"] if tipo_mercado == "future" else ["spot"]
    return {
        "enableRateLimit": True,
        "timeout": 30000,
        "options": {"defaultType": "future" if tipo_mercado == "future" else "spot",
                    "fetchMarkets": {"types": tipos}},
    }


def crear_cliente_datos(config: Config, tipo_mercado: str | None = None) -> ccxt.Exchange:
    """Cliente público (sin claves) del mercado real para descargar velas."""
    clase = getattr(ccxt, config.exchange.nombre)
    return clase(_opciones(tipo_mercado or config.exchange.mercado_datos))


def diagnosticar(error: Exception) -> str:
    """Explicación en español de los fallos de conexión más comunes."""
    texto = f"{type(error).__name__}: {error}"
    # la causa real suele venir encadenada (p. ej. un error SSL debajo del NetworkError de ccxt)
    cadena, e, vistos = [texto], error, set()
    while (e := e.__cause__ or e.__context__) is not None and id(e) not in vistos:
        vistos.add(id(e))
        cadena.append(f"{type(e).__name__}: {e}")
    todo = " | ".join(cadena).lower()
    pistas = []
    if "certificate verify failed" in todo or "sslcertverificationerror" in todo:
        pistas.append("Falla el certificado de seguridad (SSL). Suele ser un antivirus que inspecciona HTTPS "
                      "(Kaspersky, Avast, ESET...), una VPN o una red corporativa. Desactiva el 'análisis de "
                      "conexiones cifradas' del antivirus o prueba otra red.")
    elif "451" in todo or "restricted location" in todo:
        pistas.append("Binance bloquea ese servicio desde tu ubicación (error 451). Prueba con --spot.")
    elif "418" in todo or "429" in todo:
        pistas.append("Demasiadas peticiones: espera unos minutos y vuelve a ejecutar (continúa donde se quedó).")
    elif "timestamp" in todo or "recvwindow" in todo:
        pistas.append("El reloj de tu PC está desfasado: sincronízalo (Configuración → Hora e idioma → Sincronizar ahora).")
    elif isinstance(error, ccxt.ExchangeNotAvailable):  # (subclase de NetworkError: va primero)
        pistas.append("Binance no está disponible ahora o bloquea tu región. Prueba más tarde o con --spot.")
    elif isinstance(error, (ccxt.NetworkError, ccxt.RequestTimeout)):
        pistas.append("No hay conexión con Binance: revisa internet, VPN, antivirus o firewall.")
    causa = cadena[-1] if len(cadena) > 1 else ""
    if causa:
        texto += f"\n   Causa: {causa[:300]}"
    return texto + ("\n   → " + " ".join(pistas) if pistas else "")


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
