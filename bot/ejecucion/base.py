"""Interfaz común de los brokers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class ErrorEjecucion(Exception):
    """La orden no se pudo ejecutar o verificar."""


@dataclass
class Ejecucion:
    precio: float
    cantidad: float
    comision: float
    ordenes: dict = field(default_factory=dict)  # ids de órdenes en el exchange (vacío en el simulado)


@dataclass
class CierreDetectado:
    """Salida ejecutada por el propio exchange (stop o take profit que saltó allí)."""
    par: str
    precio: float
    motivo: str
    comision: float


class Broker(Protocol):
    nombre: str
    gestiona_stops: bool  # True si el exchange ejecuta SL/TP por su cuenta (demo); False si los vigila el bot

    def abrir(self, id_cliente: str, par: str, direccion: str, cantidad: float, precio_ref: float,
              stop_loss: float, take_profit: float) -> Ejecucion: ...

    def cerrar(self, id_cliente: str, par: str, direccion: str, cantidad: float, precio: float,
               motivo: str) -> Ejecucion: ...

    def cierres_en_exchange(self, abiertas: list) -> list[CierreDetectado]: ...
