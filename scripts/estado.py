"""Muestra el estado del paper trading: carteras, posiciones, resultados, últimos eventos y gasto en Claude.

Uso:  python scripts/estado.py [--diario N]   (--diario muestra el diario de las últimas N operaciones)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402

from bot.arranque import construir  # noqa: E402
from bot.db.modelos import EventoRiesgo, Operacion  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--diario", type=int, default=0)
    args = ap.parse_args()
    config, s, ciclo = construir(con_mercado=False)
    for nombre, g in ciclo.carteras.items():
        r = g.registro()
        ops = list(s.scalars(select(Operacion).where(Operacion.cartera == nombre, Operacion.estado == "cerrada")))
        pnl = sum(o.pnl_neto for o in ops)
        gan = sum(1 for o in ops if o.pnl_neto > 0)
        print(f"\n=== {nombre} === estado: {r.estado} {('(' + r.motivo + ')') if r.motivo else ''}")
        print(f"Capital realizado: {r.capital_inicial + pnl:.2f} USD (inicio {r.capital_inicial:.0f}) | "
              f"operaciones cerradas: {len(ops)} | acierto: {gan / len(ops) * 100 if ops else 0:.1f}%")
        for o in g.abiertas():
            print(f"  ABIERTA {o.direccion} {o.par} @ {o.precio_entrada:.6g} SL {o.stop_loss:.6g} TP {o.take_profit:.6g}")
    print("\nÚltimos eventos:")
    for e in s.scalars(select(EventoRiesgo).order_by(EventoRiesgo.id.desc()).limit(10)):
        print(f"  {pd.Timestamp(e.ts_ms, unit='ms'):%m-%d %H:%M} [{e.cartera}] {e.tipo} {e.par or ''}: {e.detalle[:150]}")
    if ciclo.claude:
        print(f"\nGasto en Claude este mes: {ciclo.claude.gasto_mes_usd():.4f} USD de {config.claude.presupuesto_mensual_usd}")
    if args.diario:
        for o in s.scalars(select(Operacion).order_by(Operacion.id.desc()).limit(args.diario)):
            print("\n" + "=" * 80 + f"\n#{o.id} {o.cartera} {o.par}\n{o.diario_entrada}\n{o.diario_salida or ''}\n{o.analisis_post or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
