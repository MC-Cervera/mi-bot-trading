"""Backtesting walk-forward de la estrategia técnica sola, comparado con comprar y mantener.

Uso:  python scripts/backtest.py [--pares BTC/USDT ETH/USDT] [--rapido]
Genera reportes/backtest_AAAAMMDD_HHMM.md, un CSV con todas las operaciones y otro con la curva de capital.
Requiere haber descargado el histórico (scripts/descargar_historico.py).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from bot.backtest.buy_hold import comprar_y_mantener  # noqa: E402
from bot.backtest.informe import generar_informe  # noqa: E402
from bot.backtest.metricas import calcular_metricas  # noqa: E402
from bot.backtest.motor import ParametrosBacktest  # noqa: E402
from bot.backtest.walkforward import unir_pruebas, walk_forward  # noqa: E402
from bot.config import RAIZ, cargar_config  # noqa: E402
from bot.datos.historico import cargar_velas  # noqa: E402
from bot.db import crear_motor, crear_sesion  # noqa: E402
from bot.logging_setup import configurar_logging  # noqa: E402
from bot.senales import ParametrosSenal  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pares", nargs="*")
    ap.add_argument("--rapido", action="store_true", help="rejilla reducida (pruebas rápidas, menos fiable)")
    args = ap.parse_args()

    config = cargar_config()
    configurar_logging(config.rutas.absoluta(config.rutas.logs))
    sesion = crear_sesion(crear_motor(config.rutas.absoluta(config.rutas.base_datos)))

    velas = {}
    for par in args.pares or config.pares:
        df = cargar_velas(sesion, config.exchange.nombre, par, config.temporalidad)
        if df.empty:
            print(f"[AVISO] {par}: sin histórico, se omite")
            continue
        velas[par] = df
    if not velas:
        print("No hay datos. Ejecuta primero: python scripts/descargar_historico.py")
        return 1

    wf = config.backtest.walk_forward
    if args.rapido:
        wf = wf.model_copy(update={"rejilla": wf.rejilla.model_copy(update={
            "ema": wf.rejilla.ema[:1], "atr_mult_sl": [config.estrategia.atr_mult_sl.valor],
            "ratio_tp": [config.estrategia.ratio_tp.valor]})})
    base = ParametrosSenal.desde_config(config)
    pb = ParametrosBacktest.desde_config(config)

    n_comb = len(wf.rejilla.ema) * len(wf.rejilla.multiplicador_volumen) * len(wf.rejilla.atr_mult_sl) * len(wf.rejilla.ratio_tp)
    print(f"{len(velas)} pares, {n_comb} combinaciones de parámetros. Esto puede tardar varios minutos...")
    t0 = time.time()

    def progreso(fase, k, total):
        print(f"\r  {fase}: {k}/{total}  ({time.time() - t0:.0f}s)", end="", flush=True)

    resultados = walk_forward(velas, base, pb, wf, config.temporalidad, progreso)
    print()
    if not resultados:
        meses = wf.entrenamiento_meses + wf.prueba_meses
        print(f"Histórico insuficiente: hacen falta al menos {meses} meses de datos.")
        return 1

    oos_desde, oos_hasta = resultados[0].ventana.inicio_prueba, resultados[-1].ventana.fin_prueba
    ops_opt, curva_opt = unir_pruebas(resultados)
    ops_def, curva_def = unir_pruebas(resultados, por_defecto=True)
    m_opt = calcular_metricas(ops_opt, curva_opt, pb.capital_inicial)
    m_def = calcular_metricas(ops_def, curva_def, pb.capital_inicial)
    bh = comprar_y_mantener(velas, pb.capital_inicial, oos_desde, oos_hasta, pb.comision_pct, pb.slippage_pct)
    bh_btc = comprar_y_mantener({k: v for k, v in velas.items() if k.startswith("BTC/")}, pb.capital_inicial,
                                oos_desde, oos_hasta, pb.comision_pct, pb.slippage_pct)

    todas = pd.concat([df.index.to_series() for df in velas.values()])
    informe = generar_informe({
        "m_opt": m_opt, "m_def": m_def, "bh": bh, "bh_btc": bh_btc, "ops_opt": ops_opt, "pares": list(velas),
        "desde": todas.min(), "hasta": todas.max(), "temporalidad": config.temporalidad,
        "oos_desde": oos_desde, "oos_hasta": oos_hasta, "ventanas": resultados, "capital": pb.capital_inicial,
        "pb": pb, "eventos": [e for r in resultados for e in r.prueba.eventos],
        "parada": next((e["ts"] for r in resultados for e in r.prueba.eventos if e["tipo"] == "parada_drawdown"), None),
    })

    sello = pd.Timestamp.now(tz="UTC").strftime("%Y%m%d_%H%M")
    carpeta = RAIZ / "reportes"
    carpeta.mkdir(exist_ok=True)
    ruta = carpeta / f"backtest_{sello}.md"
    ruta.write_text(informe, encoding="utf-8")
    ops_opt.to_csv(carpeta / f"backtest_{sello}_operaciones.csv", index=False)
    pd.DataFrame({"estrategia": curva_opt, "por_defecto": curva_def, "comprar_y_mantener": bh["curva"]}).to_csv(
        carpeta / f"backtest_{sello}_curvas.csv")

    print(f"\nRetorno fuera de muestra: estrategia {m_opt['retorno_pct']:+.2f}% | por defecto {m_def['retorno_pct']:+.2f}% "
          f"| comprar y mantener {bh['retorno_pct']:+.2f}%")
    print(f"Operaciones: {m_opt['operaciones']} | acierto {m_opt['tasa_acierto_pct']:.1f}% | "
          f"drawdown máx {m_opt['max_drawdown_pct']:.1f}%")
    print(f"Informe: {ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
