"""Broker simulado (paper trading): ejecuta al precio indicado con comisión y deslizamiento en contra.

No envía nada a ningún exchange. Los precios los aporta el ciclo del bot (datos reales de mercado).
"""
from __future__ import annotations

from bot.ejecucion.base import CierreDetectado, Ejecucion

# Motivos de salida que se ejecutan como orden límite (sin deslizamiento): el precio lo fijamos nosotros.
SALIDAS_LIMITE = {"take_profit"}


class BrokerSimulado:
    nombre = "simulado"
    gestiona_stops = False

    def __init__(self, comision_pct: float, slippage_pct: float):
        self.com = comision_pct / 100
        self.slip = slippage_pct / 100

    def abrir(self, id_cliente, par, direccion, cantidad, precio_ref, stop_loss, take_profit) -> Ejecucion:
        d = 1 if direccion == "largo" else -1
        precio = precio_ref * (1 + self.slip * d)
        return Ejecucion(precio=precio, cantidad=cantidad, comision=precio * cantidad * self.com)

    def cerrar(self, id_cliente, par, direccion, cantidad, precio, motivo) -> Ejecucion:
        d = 1 if direccion == "largo" else -1
        ejecutado = precio if motivo in SALIDAS_LIMITE else precio * (1 - self.slip * d)
        return Ejecucion(precio=ejecutado, cantidad=cantidad, comision=ejecutado * cantidad * self.com)

    def cierres_en_exchange(self, abiertas) -> list[CierreDetectado]:
        return []
