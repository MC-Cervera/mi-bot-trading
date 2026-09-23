"""Datos de mercado en vivo desde el exchange (endpoints públicos del mercado real)."""
from __future__ import annotations

import logging

import ccxt
import pandas as pd
from sqlalchemy.orm import Session

from bot.config import Config
from bot.datos.exchange import crear_cliente_datos, simbolo_mercado
from bot.datos.historico import actualizar_historico

log = logging.getLogger(__name__)


class MercadoCcxt:
    def __init__(self, sesion: Session, config: Config, cliente=None):
        self.s = sesion
        self.config = config
        self.cliente = cliente or crear_cliente_datos(config)
        self._simbolos = {simbolo_mercado(p, config.exchange.tipo_mercado): p for p in config.pares}

    def actualizar(self, ahora: pd.Timestamp) -> None:
        for par in self.config.pares:
            try:
                actualizar_historico(self.s, self.cliente, self.config.exchange.nombre, par, self.config.temporalidad,
                                     self.config.exchange.tipo_mercado, dias=30)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
                # el exchange no responde: no tiene sentido esperar reintentos par por par; se vuelve a intentar
                # en el próximo ciclo (las señales de pares sin la vela nueva se omiten solas)
                log.error("Exchange sin respuesta al actualizar %s (%s): se omiten los demás pares en este ciclo", par, e)
                return
            except Exception as e:  # noqa: BLE001 - un par con problemas no debe detener a los demás
                log.error("No se pudieron actualizar las velas de %s: %s", par, e)

    def precios(self) -> dict[str, float]:
        tickers = self.cliente.fetch_tickers(list(self._simbolos))
        return {self._simbolos[s]: float(t["last"]) for s, t in tickers.items() if s in self._simbolos and t.get("last")}
