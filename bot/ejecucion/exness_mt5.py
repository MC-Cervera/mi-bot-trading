"""Broker Exness vía MetaTrader 5 (solo Windows) — EXPERIMENTAL.

⚠️ La librería MetaTrader5 solo existe para Windows y necesita el terminal MT5 instalado y abierto con la cuenta.
   Este módulo se probó con un MT5 simulado; antes de usarlo ejecuta en tu PC Windows:
       python scripts/probar_exness.py            (conexión, cuenta y símbolos disponibles)
       python scripts/probar_exness.py --orden    (abre y cierra una posición mínima en DEMO)

Diseño:
- Las SEÑALES se siguen calculando con datos de Binance; Exness solo ejecuta (CFDs de cripto).
- Solo opera en cuentas DEMO: si la cuenta de MT5 es real, se niega (el modo real sigue bloqueado).
- Stop loss y take profit se envían con la orden y quedan en el servidor de Exness: si el bot se cae, la
  posición sigue protegida. Stop y objetivo se recalculan desde el precio real de ejecución manteniendo distancias.
- Volumen: cantidad (monedas) / tamaño de contrato, redondeado HACIA ABAJO al paso de lote (nunca se arriesga más
  de lo planificado). Si queda por debajo del lote mínimo, la operación no se abre y se explica por qué.
- Idempotencia: cada orden lleva un número mágico y un comentario con el id de la operación; antes de enviar se
  comprueba si ya existe una posición con ese comentario.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time

from bot.ejecucion.base import CierreDetectado, Ejecucion, ErrorEjecucion

log = logging.getLogger(__name__)

# Bits de symbol_info().filling_mode (documentación de MQL5: SYMBOL_FILLING_FOK=1, SYMBOL_FILLING_IOC=2)
BIT_FOK, BIT_IOC = 1, 2
MAX_COMENTARIO = 31  # MT5 recorta los comentarios más largos


def comentario(id_cliente: str) -> str:
    """Comentario corto y único (MT5 limita su longitud): huella del id de la operación."""
    return f"bot-{hashlib.sha1(id_cliente.encode()).hexdigest()[:16]}"


def conectar(secretos, mt5=None):
    """Inicializa MT5 con las credenciales de .env y devuelve el módulo listo para usar."""
    if mt5 is None:
        try:
            import MetaTrader5 as mt5  # noqa: N813 - nombre oficial del paquete
        except ImportError as e:
            raise ErrorEjecucion("Falta el paquete MetaTrader5 (solo Windows): pip install MetaTrader5") from e
    kwargs = {}
    if secretos.exness_terminal:
        kwargs["path"] = secretos.exness_terminal
    if secretos.exness_login:
        kwargs |= {"login": int(secretos.exness_login), "password": secretos.exness_password,
                   "server": secretos.exness_servidor}
    if not mt5.initialize(**kwargs):
        raise ErrorEjecucion(f"No se pudo conectar con MetaTrader 5: {mt5.last_error()}")
    return mt5


class BrokerExness:
    nombre = "exness_mt5"
    gestiona_stops = True
    costos_reales = True   # comisión y swap reales de Exness: el bot no simula funding encima

    def __init__(self, mt5, sufijo: str = "", simbolos: dict[str, str] | None = None, magico: int = 20260923,
                 desviacion_puntos: int = 20, permitir_real: bool = False):
        self.mt5 = mt5
        self.sufijo = sufijo
        self.simbolos = simbolos or {}
        self.magico = magico
        self.desviacion = desviacion_puntos
        cuenta = mt5.account_info()
        if cuenta is None:
            raise ErrorEjecucion(f"MT5 no devolvió datos de la cuenta: {mt5.last_error()}")
        if cuenta.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO and not permitir_real:
            raise ErrorEjecucion("La cuenta de MT5 NO es demo. El bot solo opera en demo hasta que autorices el modo "
                                 "real (y ese modo sigue bloqueado en el código).")
        self.cuenta = cuenta

    # ------------------------------------------------------------------ símbolos
    def simbolo(self, par: str) -> str:
        """'BTC/USDT' -> 'BTCUSD' + sufijo (Exness cotiza cripto contra USD). Se puede fijar a mano en config."""
        if par in self.simbolos:
            return self.simbolos[par]
        base = par.split("/")[0]
        return f"{base}USD{self.sufijo}"

    def info(self, par: str):
        sym = self.simbolo(par)
        if not self.mt5.symbol_select(sym, True):
            raise ErrorEjecucion(f"{par}: el símbolo {sym} no existe o no está disponible en esta cuenta de Exness")
        info = self.mt5.symbol_info(sym)
        if info is None:
            raise ErrorEjecucion(f"{par}: sin información del símbolo {sym}")
        if info.trade_mode != self.mt5.SYMBOL_TRADE_MODE_FULL:
            raise ErrorEjecucion(f"{par}: {sym} no admite largos y cortos ahora (modo {info.trade_mode})")
        return sym, info

    def lotes(self, info, cantidad: float) -> float:
        """Cantidad en monedas -> lotes, redondeando hacia abajo al paso permitido."""
        contrato = info.trade_contract_size or 1.0
        paso = info.volume_step or 0.01
        lotes = math.floor(cantidad / contrato / paso + 1e-9) * paso
        lotes = round(min(lotes, info.volume_max), 8)
        if lotes < info.volume_min:
            raise ErrorEjecucion(
                f"{info.name}: el tamaño que permite el riesgo ({cantidad:.6g} unidades = {cantidad / contrato:.6g} lotes) "
                f"es menor que el lote mínimo de Exness ({info.volume_min}). No se opera para no arriesgar de más.")
        return lotes

    def _relleno(self, info) -> list[int]:
        modos = []
        if info.filling_mode & BIT_FOK:
            modos.append(self.mt5.ORDER_FILLING_FOK)
        if info.filling_mode & BIT_IOC:
            modos.append(self.mt5.ORDER_FILLING_IOC)
        modos.append(self.mt5.ORDER_FILLING_RETURN)
        return modos

    def _enviar(self, peticion: dict, info) -> object:
        """Envía probando los modos de relleno admitidos; reintenta recotizaciones."""
        ultimo = None
        for relleno in self._relleno(info):
            for _ in range(3):
                tick = self.mt5.symbol_info_tick(peticion["symbol"])
                if tick is None:
                    raise ErrorEjecucion(f"Sin precio para {peticion['symbol']}")
                peticion["price"] = tick.ask if peticion["type"] == self.mt5.ORDER_TYPE_BUY else tick.bid
                peticion["type_filling"] = relleno
                r = self.mt5.order_send(peticion)
                if r is None:
                    raise ErrorEjecucion(f"order_send falló: {self.mt5.last_error()}")
                ultimo = r
                if r.retcode == self.mt5.TRADE_RETCODE_DONE:
                    return r
                if r.retcode not in (self.mt5.TRADE_RETCODE_REQUOTE, self.mt5.TRADE_RETCODE_PRICE_OFF):
                    break
                time.sleep(0.5)
            if ultimo.retcode != self.mt5.TRADE_RETCODE_INVALID_FILL:
                break
        raise ErrorEjecucion(f"Exness rechazó la orden ({ultimo.retcode}): {ultimo.comment}")

    def _posicion_existente(self, sym: str, com: str):
        for p in self.mt5.positions_get(symbol=sym) or ():
            if p.magic == self.magico and p.comment == com:
                return p
        return None

    # ------------------------------------------------------------------ interfaz Broker
    def abrir(self, id_cliente, par, direccion, cantidad, precio_ref, stop_loss, take_profit) -> Ejecucion:
        sym, info = self.info(par)
        com = comentario(id_cliente)
        existente = self._posicion_existente(sym, com)
        if existente is not None:
            log.warning("La posición %s ya existía en Exness (reintento tras fallo): no se duplica", com)
            return Ejecucion(precio=existente.price_open, cantidad=existente.volume * info.trade_contract_size,
                             comision=0.0, ordenes={"posicion": existente.ticket, "simbolo": sym})
        lotes = self.lotes(info, cantidad)
        d = 1 if direccion == "largo" else -1
        dist_sl, dist_tp = abs(precio_ref - stop_loss), abs(take_profit - precio_ref)
        tick = self.mt5.symbol_info_tick(sym)
        precio = tick.ask if d == 1 else tick.bid
        minimo = (info.trade_stops_level or 0) * info.point
        if min(dist_sl, dist_tp) <= minimo:
            raise ErrorEjecucion(f"{sym}: stop u objetivo más cerca que el mínimo de Exness ({minimo} de distancia)")
        peticion = {
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": lotes,
            "type": self.mt5.ORDER_TYPE_BUY if d == 1 else self.mt5.ORDER_TYPE_SELL,
            "sl": round(precio - d * dist_sl, info.digits), "tp": round(precio + d * dist_tp, info.digits),
            "deviation": self.desviacion, "magic": self.magico, "comment": com,
            "type_time": self.mt5.ORDER_TIME_GTC,
        }
        r = self._enviar(peticion, info)
        posicion = self._posicion_existente(sym, com)
        ticket = posicion.ticket if posicion is not None else r.order
        return Ejecucion(precio=r.price, cantidad=r.volume * info.trade_contract_size, comision=0.0,
                         ordenes={"posicion": ticket, "orden": r.order, "simbolo": sym, "lotes": r.volume})

    def cerrar(self, id_cliente, par, direccion, cantidad, precio, motivo) -> Ejecucion:
        sym, info = self.info(par)
        pos = self._posicion_existente(sym, comentario(id_cliente))
        if pos is None:
            raise ErrorEjecucion(f"No hay posición abierta en Exness para {id_cliente} (¿ya la cerró el stop?)")
        peticion = {
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": pos.volume, "position": pos.ticket,
            "type": self.mt5.ORDER_TYPE_SELL if pos.type == self.mt5.POSITION_TYPE_BUY else self.mt5.ORDER_TYPE_BUY,
            "deviation": self.desviacion, "magic": self.magico, "comment": f"cierre {motivo}"[:MAX_COMENTARIO],
            "type_time": self.mt5.ORDER_TIME_GTC,
        }
        r = self._enviar(peticion, info)
        return Ejecucion(precio=r.price, cantidad=r.volume * info.trade_contract_size,
                         comision=self._costos(pos.ticket), ordenes={"cierre": r.order})

    def _costos(self, ticket: int) -> float:
        """Comisión + swap (+ fee) de todos los deals de la posición, en positivo = costo."""
        deals = self.mt5.history_deals_get(position=ticket) or ()
        return -sum((d.commission or 0) + (d.swap or 0) + (getattr(d, "fee", 0) or 0) for d in deals)

    def cierres_en_exchange(self, abiertas) -> list[CierreDetectado]:
        """Posiciones que Exness cerró por su cuenta (saltó el stop o el objetivo en el servidor)."""
        cierres = []
        for op in abiertas:
            ticket = json.loads(op.ordenes_exchange or "{}").get("posicion")
            if ticket is None or self.mt5.positions_get(ticket=ticket):
                continue
            salidas = [d for d in (self.mt5.history_deals_get(position=ticket) or ())
                       if d.entry == self.mt5.DEAL_ENTRY_OUT]
            if not salidas:
                log.warning("Posición %s no aparece abierta ni cerrada en Exness; se revisará en el próximo ciclo", ticket)
                continue
            d = salidas[-1]
            if d.reason == self.mt5.DEAL_REASON_SL:
                motivo = "stop_loss"
            elif d.reason == self.mt5.DEAL_REASON_TP:
                motivo = "take_profit"
            else:
                sentido = 1 if op.direccion == "largo" else -1
                motivo = "take_profit" if (d.price - op.precio_entrada) * sentido > 0 else "stop_loss"
            cierres.append(CierreDetectado(par=op.par, precio=float(d.price), motivo=motivo, comision=self._costos(ticket)))
        return cierres
