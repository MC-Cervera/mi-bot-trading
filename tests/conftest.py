import copy

import pytest

from bot.config import cargar_config
from bot.db import crear_motor, crear_sesion


@pytest.fixture
def config():
    return cargar_config()


@pytest.fixture
def config_dict(config):
    return copy.deepcopy(config.model_dump())


@pytest.fixture
def sesion():
    return crear_sesion(crear_motor(":memory:"))


class ExchangeFalso:
    """Imita ccxt.fetch_ohlcv sobre una serie sintética de velas horarias."""

    def __init__(self, inicio_ms: int, n: int, tf_ms: int = 3_600_000, faltantes: set[int] | None = None, max_limite=500):
        faltantes = faltantes or set()
        self.velas = [
            [inicio_ms + i * tf_ms, 100 + i, 101 + i, 99 + i, 100.5 + i, 10.0 + i]
            for i in range(n) if i not in faltantes
        ]
        self.max_limite = max_limite
        self.llamadas = 0

    def fetch_ohlcv(self, simbolo, temporalidad, since=None, limit=None):
        self.llamadas += 1
        lim = min(limit or self.max_limite, self.max_limite)
        return [v for v in self.velas if v[0] >= since][:lim]


@pytest.fixture
def exchange_falso():
    return ExchangeFalso
