"""Valida el broker de Binance Demo paso a paso con una posición MÍNIMA (dinero de demo, no real).

Uso:  python scripts/probar_orden_demo.py [--par XRP/USDT]
Abre un largo mínimo con stop -3% y objetivo +3% (colocados en el exchange), comprueba la posición y la cierra.
Solo si todo sale OK conviene poner `ejecucion: broker: binance_demo` en config.yaml.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import cargar_config, cargar_secretos, validar_seguridad  # noqa: E402
from bot.datos.exchange import crear_cliente_cuenta, simbolo_mercado  # noqa: E402
from bot.ejecucion.binance_demo import BrokerBinanceDemo  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--par", default="XRP/USDT")
    args = ap.parse_args()
    config, secretos = cargar_config(), cargar_secretos()
    validar_seguridad(config, secretos)
    if not config.exchange.demo:
        print("Este script solo funciona con exchange.demo: true")
        return 1
    if input(f"Se abrirá y cerrará una posición mínima en DEMO ({args.par}). Escribe SI: ").strip().upper() != "SI":
        return 1
    cx = crear_cliente_cuenta(config, secretos)
    broker = BrokerBinanceDemo(cx, config.exchange.tipo_mercado)
    sym = simbolo_mercado(args.par, config.exchange.tipo_mercado)
    precio = float(cx.fetch_ticker(sym)["last"])
    mercado = cx.market(sym)
    minimo = max((mercado["limits"]["cost"] or {}).get("min") or 5, 6) * 1.2
    cantidad = max(float(cx.amount_to_precision(sym, minimo / precio)), (mercado["limits"]["amount"] or {}).get("min") or 0)
    print(f"1) Precio {precio}, cantidad {cantidad} (~{cantidad * precio:.2f} USDT)")
    ej = broker.abrir(f"prueba-{int(time.time())}", args.par, "largo", cantidad, precio, precio * 0.97, precio * 1.03)
    print(f"2) Abierta a {ej.precio} · órdenes {ej.ordenes}")
    pos = [p for p in cx.fetch_positions([sym]) if abs(float(p.get("contracts") or 0)) > 0]
    print(f"3) Posición en el exchange: {pos[0]['contracts'] if pos else 'NO ENCONTRADA'}")
    abiertas = cx.fetch_open_orders(sym, params={"trigger": True}) if pos else []
    print(f"4) Órdenes condicionales (SL/TP) activas: {len(abiertas)}")
    cierre = broker.cerrar(f"prueba-{int(time.time())}", args.par, "largo", ej.cantidad, precio, "prueba")
    print(f"5) Cerrada a {cierre.precio}")
    queda = [p for p in cx.fetch_positions([sym]) if abs(float(p.get("contracts") or 0)) > 0]
    ok = bool(pos) and not queda and len(abiertas) >= 2
    print("RESULTADO:", "OK ✅" if ok else "REVISAR ❌ (no actives binance_demo)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
