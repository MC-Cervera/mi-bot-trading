"""Prueba la capa de decisión con la última señal confirmada del histórico.

Uso:
  python scripts/probar_decision.py --par BTC/USDT            # muestra el contexto que recibiría Claude (gratis)
  python scripts/probar_decision.py --par BTC/USDT --llamar   # llama de verdad a Claude (cuesta ~0.01-0.05 USD)
No envía órdenes: solo muestra la propuesta de Claude y si pasaría las verificaciones de coherencia.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import cargar_config, cargar_secretos  # noqa: E402
from bot.datos.historico import cargar_velas  # noqa: E402
from bot.db import crear_motor, crear_sesion  # noqa: E402
from bot.ia.cliente import ClienteClaude  # noqa: E402
from bot.ia.contexto import contexto_para_senal  # noqa: E402
from bot.ia.decision import decidir  # noqa: E402
from bot.logging_setup import configurar_logging  # noqa: E402
from bot.senales import ParametrosSenal, extraer_candidatas, generar_senales  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--par", default="BTC/USDT")
    ap.add_argument("--llamar", action="store_true", help="llama a la API (tiene costo)")
    args = ap.parse_args()

    config = cargar_config()
    secretos = cargar_secretos()
    configurar_logging(config.rutas.absoluta(config.rutas.logs))
    sesion = crear_sesion(crear_motor(config.rutas.absoluta(config.rutas.base_datos)))
    velas = cargar_velas(sesion, config.exchange.nombre, args.par, config.temporalidad)
    if velas.empty:
        print("Sin histórico. Ejecuta scripts/descargar_historico.py")
        return 1
    p = ParametrosSenal.desde_config(config)
    df = generar_senales(velas, p, config.temporalidad)
    candidatas = extraer_candidatas(df, args.par, p, config.temporalidad)
    if not candidatas:
        print(f"No hay señales confirmadas en el histórico de {args.par}")
        return 1
    senal = candidatas[-1]
    ahora = senal.ts_vela + (df.index[1] - df.index[0])  # momento en que cerró la vela de la señal
    ctx = contexto_para_senal(sesion, config, senal, df, ahora, posiciones_abiertas=[],
                              capital_usd=config.capital.simulado_usd, libre_usd=config.capital.simulado_usd)
    print(ctx.a_texto())
    if not args.llamar:
        print("\n(Solo contexto. Usa --llamar para consultar a Claude.)")
        return 0
    if not secretos.anthropic_api_key:
        print("Falta ANTHROPIC_API_KEY en .env")
        return 1
    cliente = ClienteClaude(config.claude, sesion, api_key=secretos.anthropic_api_key)
    r = decidir(cliente, ctx, config.claude.esfuerzo_decision)
    print("\n=== Respuesta de Claude ===")
    print(r.decision.model_dump_json(indent=2) if r.decision else r.llamada.respuesta)
    print("Problemas de coherencia:", r.problemas or "ninguno")
    print(f"Costo: {r.llamada.costo_usd:.4f} USD ({r.llamada.tokens_entrada} entrada, {r.llamada.tokens_salida} salida, "
          f"{r.llamada.tokens_cache_lectura} de caché). Gasto del mes: {cliente.gasto_mes_usd():.4f} USD")
    return 0


if __name__ == "__main__":
    sys.exit(main())
