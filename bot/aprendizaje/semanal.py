"""Ejecución de la revisión semanal (la usan el bot programado y el script manual)."""
from __future__ import annotations

import pandas as pd

from bot.aprendizaje.motor import InformeSemanal, MotorAprendizaje
from bot.aprendizaje.revision_claude import RevisorClaude
from bot.config import RAIZ


def ejecutar_revision(sesion, config_base, ahora: pd.Timestamp, cliente_claude=None, avisos=None,
                      esfuerzo: str = "medium") -> tuple[InformeSemanal, str]:
    motor = MotorAprendizaje(sesion, config_base, ahora)
    revisor = RevisorClaude(cliente_claude, esfuerzo) if cliente_claude is not None else None
    informe = motor.ciclo_semanal(revisor)
    carpeta = RAIZ / "reportes"
    carpeta.mkdir(exist_ok=True)
    ruta = carpeta / f"aprendizaje_{ahora:%Y%m%d_%H%M}.md"
    ruta.write_text(informe.texto(), encoding="utf-8")
    if avisos is not None:
        avisos.enviar(informe.resumen_corto())
    return informe, str(ruta)
