"""Ciclo de noticias: descarga las fuentes RSS, guarda las nuevas, las analiza con Claude, mide su impacto real
en el precio y muestra estadísticas.

Uso:  python scripts/noticias.py [--sin-claude] [--estadisticas]
  --sin-claude     solo descarga y filtra (no gasta API)
  --estadisticas   solo muestra la tabla de impacto real por tema
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from bot.config import cargar_config, cargar_secretos  # noqa: E402
from bot.db import crear_motor, crear_sesion  # noqa: E402
from bot.ia.cliente import ClienteClaude, ahora_ms  # noqa: E402
from bot.logging_setup import configurar_logging  # noqa: E402
from bot.noticias.analisis import analizar_pendientes, guardar_nuevas  # noqa: E402
from bot.noticias.filtro import base_de  # noqa: E402
from bot.noticias.fuentes import descargar_fuente  # noqa: E402
from bot.noticias.impacto import estadisticas_impacto, medir_impactos  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sin-claude", action="store_true")
    ap.add_argument("--estadisticas", action="store_true")
    args = ap.parse_args()

    config = cargar_config()
    secretos = cargar_secretos()
    configurar_logging(config.rutas.absoluta(config.rutas.logs))
    sesion = crear_sesion(crear_motor(config.rutas.absoluta(config.rutas.base_datos)))
    bases = [base_de(p) for p in config.pares]
    ahora = ahora_ms()

    if not args.estadisticas:
        total = 0
        for url in config.noticias.fuentes_rss:
            crudas = descargar_fuente(url, ahora)
            nuevas = guardar_nuevas(sesion, crudas, bases, ahora)
            total += len(nuevas)
            relevantes = sum(1 for n in nuevas if not n.analizada)
            print(f"{url}: {len(crudas)} leídas, {len(nuevas)} nuevas, {relevantes} mencionan activos operados")
        if args.sin_claude:
            print("(--sin-claude: no se analizan)")
        elif not secretos.anthropic_api_key:
            print("[AVISO] Falta ANTHROPIC_API_KEY en .env: las noticias quedan pendientes de análisis")
        else:
            cliente = ClienteClaude(config.claude, sesion, api_key=secretos.anthropic_api_key)
            altas = analizar_pendientes(sesion, cliente, bases, ahora, config.claude.max_noticias_por_llamada,
                                        config.claude.esfuerzo_noticias)
            for n in altas:
                print(f"  IMPACTO ALTO [{n.tema}] sentimiento {n.sentimiento:+.2f}: {n.resumen}")
            print(f"Gasto de Claude este mes: {cliente.gasto_mes_usd():.4f} USD de {config.claude.presupuesto_mensual_usd}")
        print(f"Impactos medidos/actualizados: {medir_impactos(sesion, config.exchange.nombre, config.pares, ahora)}")

    stats = estadisticas_impacto(sesion)
    if stats.empty:
        print("\nAún no hay suficientes datos de impacto (hacen falta noticias analizadas y velas posteriores).")
    else:
        with pd.option_context("display.width", 200, "display.max_columns", 20):
            print("\nImpacto real de las noticias por tema e impacto (movimientos en %):")
            print(stats.round(2).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
