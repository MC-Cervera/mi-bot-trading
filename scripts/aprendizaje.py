"""Sistema de aprendizaje: revisión manual, listado y reversión de ajustes.

Uso:
  python scripts/aprendizaje.py revisar [--sin-claude]   # ejecuta ahora la revisión semanal
  python scripts/aprendizaje.py listar                   # hipótesis, lecciones y ajustes vigentes
  python scripts/aprendizaje.py ver H0003 | L0001        # detalle con evidencia e historial
  python scripts/aprendizaje.py revertir 4 "motivo"      # revierte el ajuste #4 a su valor anterior
El bot ejecuta la revisión solo cada domingo a las 23:30 UTC.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402

from bot.aprendizaje.ajustes import ajustes_vigentes, revertir_ajuste  # noqa: E402
from bot.aprendizaje.semanal import ejecutar_revision  # noqa: E402
from bot.arranque import construir  # noqa: E402
from bot.db.modelos import HistorialAprendizaje, Hipotesis, Leccion  # noqa: E402


def fecha(ms: int | None) -> str:
    return "—" if ms is None else f"{pd.Timestamp(ms, unit='ms'):%Y-%m-%d}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("revisar")
    r.add_argument("--sin-claude", action="store_true")
    sub.add_parser("listar")
    v = sub.add_parser("ver")
    v.add_argument("codigo")
    rv = sub.add_parser("revertir")
    rv.add_argument("ajuste_id", type=int)
    rv.add_argument("motivo")
    args = ap.parse_args()

    config, s, ciclo = construir(con_mercado=False)
    ahora = pd.Timestamp.now(tz="UTC")
    if args.cmd == "revisar":
        informe, ruta = ejecutar_revision(s, config, ahora, None if args.sin_claude else ciclo.claude, ciclo.avisos,
                                          config.claude.esfuerzo_decision)
        print(informe.texto())
        print(f"Informe guardado en {ruta}")
    elif args.cmd == "listar":
        print("HIPÓTESIS")
        for h in s.scalars(select(Hipotesis).order_by(Hipotesis.id)):
            print(f"  {h.codigo} [{h.estado:<10}] ({h.origen}) {h.enunciado}")
        print("\nLECCIONES")
        for l in s.scalars(select(Leccion).order_by(Leccion.id)):
            print(f"  {l.codigo} [{l.estado:<11}] {l.enunciado}")
        print("\nAJUSTES VIGENTES")
        for a in ajustes_vigentes(s):
            print(f"  #{a.id} {a.parametro}: {a.valor_anterior:g} → {a.valor_nuevo:g} ({fecha(a.creado_ms)})")
    elif args.cmd == "ver":
        cod = args.codigo.upper()
        modelo, entidad = (Hipotesis, "hipotesis") if cod.startswith("H") else (Leccion, "leccion")
        obj = s.scalar(select(modelo).where(modelo.codigo == cod))
        if obj is None:
            print("No existe")
            return 1
        print(f"{obj.codigo} [{obj.estado}] {obj.enunciado}\n\n{obj.explicacion}\n\nEstado: {obj.motivo_estado}")
        for campo in ("evidencia_backtest", "evidencia_adelante", "evidencia"):
            if getattr(obj, campo, None):
                print(f"\n{campo}:\n{json.dumps(json.loads(getattr(obj, campo)), indent=2, ensure_ascii=False)}")
        print("\nHistorial:")
        for e in s.scalars(select(HistorialAprendizaje).where(HistorialAprendizaje.entidad == entidad,
                                                              HistorialAprendizaje.entidad_id == obj.id)):
            print(f"  {fecha(e.ts_ms)} {e.evento}: {e.detalle}")
    elif args.cmd == "revertir":
        a = revertir_ajuste(s, args.ajuste_id, f"Revertido manualmente: {args.motivo}", int(ahora.timestamp() * 1000))
        print(f"Revertido: {a.parametro} vuelve a {a.valor_anterior:g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
