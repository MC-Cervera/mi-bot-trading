"""Arranca el bot en PAPER TRADING (no usa dinero real).

Uso:  python scripts/bot.py            # corre indefinidamente (Ctrl+C para salir)
      python scripts/bot.py --una-vez  # ejecuta un ciclo completo y termina (para probar)

Detener de emergencia (cierra todo): en otra ventana, python scripts/emergencia.py
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from apscheduler.executors.pool import ThreadPoolExecutor  # noqa: E402
from apscheduler.schedulers.blocking import BlockingScheduler  # noqa: E402

from bot.aprendizaje.semanal import ejecutar_revision  # noqa: E402
from bot import proceso  # noqa: E402
from bot.arranque import ARCHIVO_DETENER, construir  # noqa: E402
from bot.respaldo import respaldar  # noqa: E402

log = logging.getLogger("bot")


def ahora() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--una-vez", action="store_true")
    args = ap.parse_args()
    if ARCHIVO_DETENER.exists():
        print(f"El bot está detenido por emergencia ({ARCHIVO_DETENER}). Usa scripts/reactivar.py tras revisar.")
        return 3  # código propio: el servicio del VPS no lo reinicia (RestartPreventExitStatus=3)
    otro = proceso.estado()
    if not args.una_vez and otro.en_marcha and otro.pid != os.getpid():
        print(f"Ya hay un bot en marcha (PID {otro.pid}). No se inicia otro para no duplicar operaciones.\n"
              "Si acabas de cerrarlo, espera 2-3 minutos y vuelve a intentarlo.")
        return 4
    config, sesion, ciclo = construir()
    if config.modo != "paper":
        print("Este script solo corre en modo paper.")
        return 1
    if ciclo.claude is None:
        print("[AVISO] Sin ANTHROPIC_API_KEY: la cartera 'tecnico_claude' no abrirá operaciones.")

    errores = {"seguidos": 0}

    def seguro(nombre, funcion):
        def envoltura():
            if ARCHIVO_DETENER.exists():
                log.warning("Archivo DETENER encontrado: apagando el bot")
                programador.shutdown(wait=False)
                return
            try:
                funcion(ahora())
                errores["seguidos"] = 0
            except Exception as e:  # noqa: BLE001 - un fallo en un ciclo no debe tumbar el bot
                errores["seguidos"] += 1
                log.exception("Error en %s", nombre)
                if errores["seguidos"] in (3, 10, 50):
                    ciclo.avisos.enviar(f"⚠️ {errores['seguidos']} errores seguidos en el bot ({nombre}): {e}")
        return envoltura

    if args.una_vez:
        for nombre, f in (("noticias", ciclo.ciclo_noticias), ("ciclo", ciclo.ciclo_horario)):
            seguro(nombre, f)()
        print("Ciclo completado. Revisa: python scripts/estado.py")
        return 0

    # un solo hilo: los trabajos nunca se solapan (comparten la conexión a la base de datos)
    # "default": un solo hilo para el trabajo real (nunca se solapa: comparte la base de datos).
    # "latido": hilo aparte, para que un ciclo largo no haga parecer que el bot se cayó.
    programador = BlockingScheduler(timezone="UTC",
                                    executors={"default": ThreadPoolExecutor(1), "latido": ThreadPoolExecutor(1)},
                                    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300})
    proceso.limpiar_parada()
    inicio = ahora().timestamp()
    proceso.escribir_latido(iniciado=inicio)

    def latido():
        if proceso.parada_solicitada():
            log.warning("Parada solicitada desde el panel: apagando el bot (las posiciones siguen abiertas)")
            proceso.limpiar_parada()
            programador.shutdown(wait=False)
            return
        proceso.escribir_latido(iniciado=inicio)

    programador.add_job(latido, "interval", seconds=proceso.SEGUNDOS_LATIDO, executor="latido")
    programador.add_job(seguro("ciclo", ciclo.ciclo_horario), "cron", minute=config.ejecucion.minuto_ciclo, second=5)
    programador.add_job(seguro("monitor", ciclo.monitor), "interval", seconds=config.ejecucion.segundos_monitor)
    programador.add_job(seguro("noticias", ciclo.ciclo_noticias), "interval", minutes=config.noticias.intervalo_minutos,
                        next_run_time=ahora().to_pydatetime())
    def aprendizaje(t):
        ejecutar_revision(sesion, ciclo.config_base, t, ciclo.claude, ciclo.avisos, config.claude.esfuerzo_decision)

    def respaldo(t):
        respaldar(config.rutas.absoluta(config.rutas.base_datos), ahora=t)

    programador.add_job(seguro("respaldo", respaldo), "cron", hour=23, minute=45)  # diario, tras el cierre
    # revisión semanal de aprendizaje: domingos 23:30 UTC (después del cierre diario, sin posiciones abiertas)
    programador.add_job(seguro("aprendizaje", aprendizaje), "cron", day_of_week="sun", hour=23, minute=30)
    ciclo.avisos.enviar("🤖 Bot iniciado en PAPER TRADING (carteras: tecnico_claude y tecnico_solo)")
    print("Bot en marcha (paper trading). Ctrl+C para salir.")
    try:
        programador.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        proceso.borrar_latido()
    ciclo.avisos.enviar("⏹️ Bot apagado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
