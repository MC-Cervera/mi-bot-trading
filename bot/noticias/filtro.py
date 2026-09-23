"""Filtro barato por palabras clave: solo las noticias que mencionan activos operados (o el mercado en general)
se envían a Claude. Así se ahorra costo de API."""
from __future__ import annotations

import re

MERCADO = "MERCADO"

# Nombres (sin distinguir mayúsculas) y tickers (en MAYÚSCULAS exactas, para no confundir "link", "dot", "uni"...).
NOMBRES = {
    "BTC": ["bitcoin"], "ETH": ["ethereum", "ether"], "BNB": ["bnb chain", "binance coin"], "SOL": ["solana"],
    "XRP": ["ripple"], "ADA": ["cardano"], "LINK": ["chainlink"], "AVAX": ["avalanche"], "LTC": ["litecoin"],
    "DOT": ["polkadot"], "TRX": ["tron"], "BCH": ["bitcoin cash"], "XLM": ["stellar"], "ATOM": ["cosmos hub", "cosmos"],
    "UNI": ["uniswap"],
}
TEMAS_MERCADO = [
    "sec", "federal reserve", "fed", "fomc", "powell", "interest rate", "rate cut", "rate hike", "inflation", "cpi",
    "etf", "regulation", "regulator", "crypto market", "stablecoin", "tariff", "recession", "treasury", "liquidation",
    "hack", "exploit", "binance", "coinbase", "blackrock",
]


def _patron_palabras(palabras: list[str]) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(re.escape(p) for p in palabras) + r")\b", re.IGNORECASE)


_NOMBRES_RE = {base: _patron_palabras(n) for base, n in NOMBRES.items()}
_MERCADO_RE = _patron_palabras(TEMAS_MERCADO)


def detectar_activos(texto: str, bases: list[str]) -> list[str]:
    """Activos operados mencionados en el texto; añade MERCADO si trata temas que mueven a todo el mercado."""
    encontrados = []
    for base in bases:
        ticker = re.search(rf"(?<![A-Za-z$]){re.escape(base)}(?![A-Za-z])", texto)
        ticker = ticker or re.search(rf"\${re.escape(base)}\b", texto)
        nombre = _NOMBRES_RE.get(base)
        if ticker or (nombre and nombre.search(texto)):
            encontrados.append(base)
    if _MERCADO_RE.search(texto):
        encontrados.append(MERCADO)
    return encontrados


def base_de(par: str) -> str:
    return par.split("/")[0]
