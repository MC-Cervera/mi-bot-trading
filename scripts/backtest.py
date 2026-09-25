"""Backtesting walk-forward de la estrategia técnica sola, comparado con comprar y mantener.

Uso:  python scripts/backtest.py [--pares BTC/USDT ETH/USDT] [--rapido] [--temporalidad 5m 15m 30m 1h]
Genera reportes/backtest_AAAAMMDD_HHMM.md, un CSV con todas las operaciones y otro con la curva de capital.
Con varias temporalidades genera un informe por cada una (backtest_5m_..., backtest_15m_...) y
reportes/backtest_comparacion_AAAAMMDD_HHMM.md, que las compara en una sola tabla.
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
from bot.backtest.informe import generar_informe, informe_comparativo  # noqa: E402
from bot.backtest.metricas import calcular_metricas  # noqa: E402
from bot.backtest.motor import ParametrosBacktest  # noqa: E402
from bot.backtest.walkforward import unir_pruebas, walk_forward  # noqa: E402
from bot.config import RAIZ, cargar_config  # noqa: E402
from bot.datos.historico import cargar_velas  # noqa: E402
from bot.db import crear_motor, crear_sesion  # noqa: E402
from bot.logging_setup import configurar_logging  # noqa: E402
from bot.senales import ParametrosSenal  # noqa: E402


TEMPORALIDADES = ["5m", "15m", "30m", "1h", "4h"]


def correr(config, tf: str, pares: list[str], rapido: bool, sello: str) -> dict | None:
    """Backtest walk-forward de una temporalidad. Escribe su informe y devuelve el resumen (None si no hay datos)."""
    ruta_db = config.ruta_velas(tf)
    if not ruta_db.exists():
        print(f"[{tf}] No hay velas de {tf} ({ruta_db} no existe).\n"
              f"      Descárgalas con: python scripts/descargar_historico.py --temporalidad {tf}")
        return None
    sesion = crear_sesion(crear_motor(ruta_db))
    velas = {}
    for par in pares:
        df = cargar_velas(sesion, config.exchange.nombre, par, tf)
        if df.empty:
            print(f"[AVISO] {par} {tf}: sin histórico, se omite")
            continue
        velas[par] = df
    if not velas:
        print(f"[{tf}] No hay velas de {tf} en {ruta_db}.\n"
              f"      Descárgalas con: python scripts/descargar_historico.py --temporalidad {tf}")
        return None

    wf = config.backtest.walk_forward
    if rapido:
        wf = wf.model_copy(update={"rejilla": wf.rejilla.model_copy(update={
            "ema": wf.rejilla.ema[:1], "atr_mult_sl": [config.estrategia.atr_mult_sl.valor],
            "ratio_tp": [config.estrategia.ratio_tp.valor]})})
    base = ParametrosSenal.desde_config(config)
    pb = ParametrosBacktest.desde_config(config)

    n_comb = len(wf.rejilla.ema) * len(wf.rejilla.multiplicador_volumen) * len(wf.rejilla.atr_mult_sl) * len(wf.rejilla.ratio_tp)
    print(f"[{tf}] {len(velas)} pares, {n_comb} combinaciones de parámetros. Esto puede tardar varios minutos...")
    t0 = time.time()

    def progreso(fase, k, total):
        print(f"\r  {fase}: {k}/{total}  ({time.time() - t0:.0f}s)", end="", flush=True)

    resultados = walk_forward(velas, base, pb, wf, tf, progreso)
    print()
    if not resultados:
        meses = wf.entrenamiento_meses + wf.prueba_meses
        print(f"[{tf}] Histórico insuficiente: hacen falta al menos {meses} meses de datos.")
        return None

    oos_desde, oos_hasta = resultados[0].ventana.inicio_prueba, resultados[-1].ventana.fin_prueba
    ops_opt, curva_opt = unir_pruebas(resultados)
    ops_def, curva_def = unir_pruebas(resultados, por_defecto=True)
    m_opt = calcular_metricas(ops_opt, curva_opt, pb.capital_inicial)
    m_def = calcular_metricas(ops_def, curva_def, pb.capital_inicial)
    bh = comprar_y_mantener(velas, pb.capital_inicial, oos_desde, oos_hasta, pb.comision_pct, pb.slippage_pct)
    bh_btc = comprar_y_mantener({k: v for k, v in velas.items() if k.startswith("BTC/")}, pb.capital_inicial,
                                oos_desde, oos_hasta, pb.comision_pct, pb.slippage_pct)
    eventos = [e for r in resultados for e in r.prueba.eventos]
    parada = next((e["ts"] for e in eventos if e["tipo"] == "parada_drawdown"), None)

    todas = pd.concat([df.index.to_series() for df in velas.values()])
    informe = generar_informe({
        "m_opt": m_opt, "m_def": m_def, "bh": bh, "bh_btc": bh_btc, "ops_opt": ops_opt, "pares": list(velas),
        "desde": todas.min(), "hasta": todas.max(), "temporalidad": tf,
        "oos_desde": oos_desde, "oos_hasta": oos_hasta, "ventanas": resultados, "capital": pb.capital_inicial,
        "pb": pb, "eventos": eventos, "parada": parada,
    })

    carpeta = RAIZ / "reportes"
    carpeta.mkdir(exist_ok=True)
    nombre = f"backtest_{sello}" if tf == config.temporalidad else f"backtest_{tf}_{sello}"
    ruta = carpeta / f"{nombre}.md"
    ruta.write_text(informe, encoding="utf-8")
    ops_opt.to_csv(carpeta / f"{nombre}_operaciones.csv", index=False)
    pd.DataFrame({"estrategia": curva_opt, "por_defecto": curva_def, "comprar_y_mantener": bh["curva"]}).to_csv(
        carpeta / f"{nombre}_curvas.csv")

    print(f"[{tf}] Retorno fuera de muestra: estrategia {m_opt['retorno_pct']:+.2f}% | por defecto "
          f"{m_def['retorno_pct']:+.2f}% | comprar y mantener {bh['retorno_pct']:+.2f}%")
    print(f"[{tf}] Operaciones: {m_opt['operaciones']} | acierto {m_opt['tasa_acierto_pct']:.1f}% | "
          f"drawdown máx {m_opt['max_drawdown_pct']:.1f}%")
    print(f"[{tf}] Informe: {ruta}")
    return {"temporalidad": tf, "m_opt": m_opt, "m_def": m_def, "bh": bh, "ops": ops_opt, "eventos": eventos,
            "parada": parada, "dias": (oos_hasta - oos_desde).days, "ruta": ruta}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pares", nargs="*")
    ap.add_argument("--rapido", action="store_true", help="rejilla reducida (pruebas rápidas, menos fiable)")
    ap.add_argument("--temporalidad", nargs="*", choices=TEMPORALIDADES,
                    help="por defecto la de config.yaml; con varias se genera además un informe comparativo")
    args = ap.parse_args()

    config = cargar_config()
    configurar_logging(config.rutas.absoluta(config.rutas.logs))
    sello = pd.Timestamp.now(tz="UTC").strftime("%Y%m%d_%H%M")
    temporalidades = args.temporalidad or [config.temporalidad]

    resumenes = []
    for tf in temporalidades:
        r = correr(config, tf, args.pares or config.pares, args.rapido, sello)
        if r:
            resumenes.append(r)
    if not resumenes:
        print("Ejecuta primero: python scripts/descargar_historico.py  (y revisa que termine sin [ERROR]).")
        return 1
    if len(temporalidades) > 1:
        ruta = RAIZ / "reportes" / f"backtest_comparacion_{sello}.md"
        ruta.write_text(informe_comparativo(resumenes, ParametrosBacktest.desde_config(config)), encoding="utf-8")
        print(f"\nComparación de temporalidades: {ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
