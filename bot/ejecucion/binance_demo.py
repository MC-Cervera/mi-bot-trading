"""Broker Binance Demo (demo.binance.com) vía ccxt — EXPERIMENTAL.

⚠️ No se ha podido probar contra el exchange desde el entorno de desarrollo. Antes de usarlo, ejecuta
   scripts/probar_orden_demo.py, que abre y cierra una posición mínima y comprueba cada paso.

- Apalancamiento 1x y margen aislado por par.
- Entrada a mercado con clientOrderId = id de la operación: si hay un fallo de red, antes de reintentar se consulta
  si la orden ya existe, así nunca se duplica.
- Stop loss y take profit se colocan en el exchange como órdenes condicionales reduceOnly: si el bot se cae,
  la posición sigue protegida.
- Tras cada orden se verifica su estado con fetch_order.
"""
from __future__ import annotations

import logging
import re
import time

import ccxt

from bot.datos.exchange import simbolo_mercado
from bot.ejecucion.base import CierreDetectado, Ejecucion, ErrorEjecucion

log = logging.getLogger(__name__)


def id_binance(id_cliente: str, sufijo: str = "") -> str:
    """Binance acepta ^[.A-Z:/a-z0-9_-]{1,36}$."""
    limpio = re.sub(r"[^.A-Za-z0-9:/_-]", "_", id_cliente)
    return (limpio[: 36 - len(sufijo)] + sufijo)[:36]


class BrokerBinanceDemo:
    nombre = "binance_demo"
    gestiona_stops = True

    def __init__(self, cliente: ccxt.Exchange, tipo_mercado: str = "future"):
        self.cx = cliente
        self.tipo = tipo_mercado
        self.cx.load_markets()

    def _sym(self, par: str) -> str:
        return simbolo_mercado(par, self.tipo)

    def _buscar(self, sym: str, cid: str):
        try:
            return self.cx.fetch_order(None, sym, {"origClientOrderId": cid})
        except ccxt.OrderNotFound:
            return None

    def _mercado(self, sym: str, lado: str, cantidad: float, cid: str, reduce_only: bool = False) -> dict:
        existente = self._buscar(sym, cid)
        if existente is not None:
            log.warning("La orden %s ya existía (reintento tras fallo): no se duplica", cid)
            return existente
        params = {"clientOrderId": cid}
        if reduce_only:
            params["reduceOnly"] = True
        for intento in range(3):
            try:
                orden = self.cx.create_order(sym, "market", lado, cantidad, None, params)
                break
            except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                log.warning("Error de red enviando %s (intento %d): %s", cid, intento + 1, e)
                time.sleep(2 ** intento)
                orden = self._buscar(sym, cid)
                if orden is not None:
                    break
        else:
            raise ErrorEjecucion(f"No se pudo enviar la orden {cid}")
        for _ in range(5):  # verificar que se ejecutó
            o = self.cx.fetch_order(orden["id"], sym)
            if o.get("status") == "closed" and (o.get("filled") or 0) > 0:
                return o
            time.sleep(1)
        raise ErrorEjecucion(f"La orden {cid} no aparece ejecutada: estado {o.get('status')}")

    def abrir(self, id_cliente, par, direccion, cantidad, precio_ref, stop_loss, take_profit) -> Ejecucion:
        sym = self._sym(par)
        try:
            self.cx.set_margin_mode("isolated", sym)
        except ccxt.ExchangeError as e:  # ya estaba en aislado
            log.debug("set_margin_mode: %s", e)
        self.cx.set_leverage(1, sym)
        cant = float(self.cx.amount_to_precision(sym, cantidad))
        lado, lado_cierre = ("buy", "sell") if direccion == "largo" else ("sell", "buy")
        o = self._mercado(sym, lado, cant, id_binance(id_cliente, "-in"))
        precio = float(o.get("average") or o.get("price") or precio_ref)
        llenado = float(o.get("filled") or cant)
        ordenes = {"entrada": o["id"]}
        try:
            sl = self.cx.create_order(sym, "market", lado_cierre, llenado, None, {
                "stopLossPrice": float(self.cx.price_to_precision(sym, stop_loss)), "reduceOnly": True,
                "clientOrderId": id_binance(id_cliente, "-sl")})
            tp = self.cx.create_order(sym, "market", lado_cierre, llenado, None, {
                "takeProfitPrice": float(self.cx.price_to_precision(sym, take_profit)), "reduceOnly": True,
                "clientOrderId": id_binance(id_cliente, "-tp")})
            ordenes |= {"stop_loss": sl.get("id"), "take_profit": tp.get("id")}
        except Exception as e:  # noqa: BLE001 - sin stop no se permite seguir en la posición
            log.error("No se pudo colocar SL/TP en %s: %s. Se cierra la posición por seguridad.", par, e)
            self._mercado(sym, lado_cierre, llenado, id_binance(id_cliente, "-xx"), reduce_only=True)
            raise ErrorEjecucion(f"Sin stop loss en el exchange para {par}: posición cerrada por seguridad") from e
        comision = sum(float(f.get("cost") or 0) for f in (o.get("fees") or [])) or precio * llenado * 0.0005
        return Ejecucion(precio=precio, cantidad=llenado, comision=comision, ordenes=ordenes)

    def _cancelar_condicionales(self, sym: str) -> None:
        for params in ({}, {"trigger": True}):
            try:
                self.cx.cancel_all_orders(sym, params)
            except Exception as e:  # noqa: BLE001
                log.debug("cancel_all_orders(%s, %s): %s", sym, params, e)

    def cerrar(self, id_cliente, par, direccion, cantidad, precio, motivo) -> Ejecucion:
        sym = self._sym(par)
        self._cancelar_condicionales(sym)
        lado = "sell" if direccion == "largo" else "buy"
        o = self._mercado(sym, lado, float(self.cx.amount_to_precision(sym, cantidad)), id_binance(id_cliente, "-out"),
                          reduce_only=True)
        p = float(o.get("average") or o.get("price") or precio)
        comision = sum(float(f.get("cost") or 0) for f in (o.get("fees") or [])) or p * cantidad * 0.0005
        return Ejecucion(precio=p, cantidad=float(o.get("filled") or cantidad), comision=comision, ordenes={"salida": o["id"]})

    def cierres_en_exchange(self, abiertas) -> list[CierreDetectado]:
        """Posiciones que el exchange cerró solo (saltó el SL o el TP)."""
        if not abiertas:
            return []
        simbolos = [self._sym(op.par) for op in abiertas]
        vivas = {p["symbol"] for p in self.cx.fetch_positions(simbolos) if abs(float(p.get("contracts") or 0)) > 0}
        cierres = []
        for op in abiertas:
            sym = self._sym(op.par)
            if sym in vivas:
                continue
            trades = self.cx.fetch_my_trades(sym, since=op.ts_entrada_ms)
            lado_cierre = "sell" if op.direccion == "largo" else "buy"
            salidas = [t for t in trades if t["side"] == lado_cierre]
            precio = salidas[-1]["price"] if salidas else float(self.cx.fetch_ticker(sym)["last"])
            d = 1 if op.direccion == "largo" else -1
            motivo = "take_profit" if (precio - op.precio_entrada) * d > 0 else "stop_loss"
            comision = sum(float((t.get("fee") or {}).get("cost") or 0) for t in salidas)
            self._cancelar_condicionales(sym)  # la otra orden condicional queda huérfana: se cancela
            cierres.append(CierreDetectado(par=op.par, precio=float(precio), motivo=motivo, comision=comision))
        return cierres
