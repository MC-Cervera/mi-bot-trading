"""Valida la conexión con Exness (MetaTrader 5, solo Windows) y qué pares se pueden operar con tus reglas de riesgo.

Uso:
  python scripts/probar_exness.py                       # cuenta, símbolos y compatibilidad de lotes (no opera)
  python scripts/probar_exness.py --orden ETH/USDT      # abre y cierra una posición mínima en DEMO
Requisitos: MetaTrader 5 instalado y abierto con tu cuenta DEMO de Exness, `pip install MetaTrader5`,
y EXNESS_LOGIN / EXNESS_PASSWORD / EXNESS_SERVIDOR en .env.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import cargar_config, cargar_secretos  # noqa: E402
from bot.dimensionamiento import riesgo_permitido_usd  # noqa: E402
from bot.ejecucion.base import ErrorEjecucion  # noqa: E402
from bot.ejecucion.exness_mt5 import BrokerExness, conectar  # noqa: E402

STOP_TIPICO_PCT = 1.5  # distancia de stop habitual con 1.5 x ATR en velas de 1 h (aproximada)


def compatibilidad(b: BrokerExness, par: str, capital: float, config) -> str:
    try:
        sym, info = b.info(par)
    except ErrorEjecucion as e:
        return f"{par:<10} ❌ {e}"
    tick = b.mt5.symbol_info_tick(sym)
    precio = tick.ask
    contrato = info.trade_contract_size or 1.0
    nocional_min = info.volume_min * contrato * precio
    nocional_max = capital * config.riesgo.apalancamiento / config.riesgo.max_posiciones
    riesgo = riesgo_permitido_usd(capital, config.riesgo.sl_usd_por_operacion, config.riesgo.riesgo_max_pct_operacion)
    # nocional que permite el riesgo con un stop típico
    nocional_riesgo = riesgo / (STOP_TIPICO_PCT / 100)
    nocional_posible = min(nocional_riesgo, nocional_max)
    ok = nocional_posible >= nocional_min
    return (f"{par:<10} {'✅' if ok else '❌'} {sym:<10} lote mín {info.volume_min:g} (= {nocional_min:,.0f} USD) · "
            f"permitido ~{nocional_posible:,.0f} USD (riesgo {riesgo:.2f} USD con stop {STOP_TIPICO_PCT}%, "
            f"tope {nocional_max:,.0f} USD/posición)" + ("" if ok else "  → el lote mínimo es demasiado grande"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--orden", metavar="PAR", help="abre y cierra una posición mínima en ese par (solo DEMO)")
    args = ap.parse_args()
    config, secretos = cargar_config(), cargar_secretos()
    try:
        mt5 = conectar(secretos)
        e = config.exness
        b = BrokerExness(mt5, e.sufijo_simbolo, e.simbolos, e.magico, e.desviacion_puntos)
    except ErrorEjecucion as err:
        print(f"[ERROR] {err}")
        return 1
    c = b.cuenta
    print(f"Cuenta {c.login} en {c.server}: DEMO ✅ · saldo {c.balance:,.2f} {getattr(c, 'currency', '')} · "
          f"apalancamiento de la cuenta 1:{getattr(c, 'leverage', '?')} (el bot usa como máximo 1x)")
    capital = config.capital.simulado_usd
    if abs(c.balance - capital) > capital * 0.2:
        print(f"[AVISO] El saldo demo ({c.balance:,.0f}) difiere del capital simulado del bot ({capital:,.0f}). "
              "Crea la cuenta demo con un saldo parecido para que el tamaño de las operaciones sea realista.")
    print(f"\nCompatibilidad de cada par con tus reglas (capital {capital:,.0f} USD):")
    filas = [compatibilidad(b, par, capital, config) for par in config.pares]
    print("\n".join(filas))
    print(f"\n{sum('✅' in f for f in filas)} de {len(filas)} pares se pueden operar. Los ❌ se bloquearán solos "
          "(el bot nunca redondea el lote hacia arriba). Si algún símbolo no aparece, revisa `exness.sufijo_simbolo` "
          "o `exness.simbolos` en config.yaml.")
    if not args.orden:
        return 0
    if input(f"\nSe abrirá y cerrará una posición MÍNIMA en DEMO ({args.orden}). Escribe SI: ").strip().upper() != "SI":
        return 1
    sym, info = b.info(args.orden)
    precio = mt5.symbol_info_tick(sym).ask
    cantidad = info.volume_min * info.trade_contract_size
    id_prueba = f"prueba-{args.orden.replace('/', '')}-{int(time.time())}"
    ej = b.abrir(id_prueba, args.orden, "largo", cantidad, precio, precio * 0.97, precio * 1.03)
    print(f"1) Abierta a {ej.precio} · {ej.ordenes}")
    pos = mt5.positions_get(ticket=ej.ordenes["posicion"])
    print(f"2) En MT5: {'posición encontrada con SL ' + str(pos[0].sl) + ' y TP ' + str(pos[0].tp) if pos else 'NO ENCONTRADA'}")
    cierre = b.cerrar(id_prueba, args.orden, "largo", cantidad, precio, "prueba")
    print(f"3) Cerrada a {cierre.precio} · costos reales {cierre.comision:.4f}")
    queda = mt5.positions_get(ticket=ej.ordenes["posicion"])
    ok = bool(pos) and pos[0].sl > 0 and pos[0].tp > 0 and not queda
    print("RESULTADO:", "OK ✅ (puedes poner ejecucion.broker: exness_demo)" if ok else "REVISAR ❌ (no actives exness_demo)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
