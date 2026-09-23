"""Botón de emergencia (lo usan scripts/emergencia.py y el panel): cierra todo y detiene el bot."""
from __future__ import annotations

import pandas as pd

from bot.arranque import ARCHIVO_DETENER, construir


def activar_emergencia(motivo: str = "Botón de emergencia") -> dict:
    """Crea el archivo DETENER (el proceso del bot se apaga solo), cierra posiciones y detiene las carteras.

    Devuelve {cartera: [pares que no se pudieron cerrar]} y un posible error de precios.
    """
    ARCHIVO_DETENER.parent.mkdir(exist_ok=True)
    ARCHIVO_DETENER.write_text(f"{motivo} — {pd.Timestamp.now(tz='UTC')}\n", encoding="utf-8")
    _, _, ciclo = construir()
    ahora = pd.Timestamp.now(tz="UTC")
    error = None
    try:
        precios = ciclo.mercado.precios()
    except Exception as e:  # noqa: BLE001
        error = f"No se pudieron obtener precios ({e}). El bot queda detenido; revisa las posiciones abiertas."
        precios = {}
    pendientes = {}
    for nombre, g in ciclo.carteras.items():
        g.emergencia(precios, ahora, motivo)
        pendientes[nombre] = [o.par for o in g.abiertas()]
    return {"pendientes": pendientes, "error": error}
