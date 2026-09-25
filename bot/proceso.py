"""Control del proceso del bot desde el panel: iniciar, pedir que se detenga y saber si está vivo.

- Latido: el bot escribe data/latido.json cada 30 s. Si el latido es reciente, el bot está en marcha.
- Detener: el panel crea data/PARAR; el bot lo ve en el siguiente latido y se apaga ordenadamente.
  NO cierra posiciones (para eso está el botón de emergencia). Al volver a iniciarlo, retoma la vigilancia.
- Nunca se arrancan dos bots a la vez: si hay un latido reciente, iniciar se rechaza.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from bot.config import RAIZ

ARCHIVO_LATIDO = RAIZ / "data" / "latido.json"
ARCHIVO_PARAR = RAIZ / "data" / "PARAR"
ARCHIVO_CONSOLA = RAIZ / "logs" / "bot_consola.log"
SEGUNDOS_LATIDO = 30
LATIDO_VIGENTE_S = 150   # sin latido durante más de 2.5 min = el bot no está corriendo


@dataclass
class EstadoProceso:
    en_marcha: bool
    pid: int | None
    segundos_desde_latido: float | None
    iniciado: float | None
    parada_solicitada: bool


def escribir_latido(pid: int | None = None, iniciado: float | None = None, ruta: Path = ARCHIVO_LATIDO) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    previo = leer_latido(ruta) or {}
    datos = {"pid": pid or os.getpid(), "ts": time.time(),
             "iniciado": iniciado or previo.get("iniciado") or time.time()}
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(json.dumps(datos), encoding="utf-8")
    os.replace(temporal, ruta)  # escritura atómica: el panel nunca lee un archivo a medias


def leer_latido(ruta: Path = ARCHIVO_LATIDO) -> dict | None:
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def borrar_latido(ruta: Path = ARCHIVO_LATIDO) -> None:
    ruta.unlink(missing_ok=True)


def estado(ahora: float | None = None, ruta_latido: Path = ARCHIVO_LATIDO, ruta_parar: Path = ARCHIVO_PARAR) -> EstadoProceso:
    ahora = ahora or time.time()
    lat = leer_latido(ruta_latido)
    if not lat:
        return EstadoProceso(False, None, None, None, ruta_parar.exists())
    edad = ahora - lat.get("ts", 0)
    return EstadoProceso(edad <= LATIDO_VIGENTE_S, lat.get("pid"), edad, lat.get("iniciado"), ruta_parar.exists())


def pedir_parada(ruta: Path = ARCHIVO_PARAR) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(f"Parada solicitada {time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")


def parada_solicitada(ruta: Path = ARCHIVO_PARAR) -> bool:
    return ruta.exists()


def limpiar_parada(ruta: Path = ARCHIVO_PARAR) -> None:
    ruta.unlink(missing_ok=True)


def iniciar(popen=subprocess.Popen) -> int:
    """Arranca scripts/bot.py como proceso independiente (sigue vivo aunque se cierre el panel). Devuelve el PID."""
    if estado().en_marcha:
        raise RuntimeError("El bot ya está en marcha: no se inicia otro.")
    limpiar_parada()
    ARCHIVO_CONSOLA.parent.mkdir(parents=True, exist_ok=True)
    salida = open(ARCHIVO_CONSOLA, "a", encoding="utf-8")  # noqa: SIM115 - lo hereda el proceso hijo
    opciones = {"cwd": str(RAIZ), "stdout": salida, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        # sin ventana y desacoplado del panel
        opciones["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        opciones["start_new_session"] = True
    proc = popen([sys.executable, str(RAIZ / "scripts" / "bot.py")], **opciones)
    return proc.pid
