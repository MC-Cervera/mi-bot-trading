"""Informe comparativo honesto del paper trading:
técnico solo  vs.  técnico + Claude (con y sin su costo)  vs.  comprar y mantener, en el MISMO periodo.

Si Claude no mejora los resultados, el informe lo dice sin rodeos.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bot.aprendizaje.estadistica import p_valor_permutacion
from bot.backtest.buy_hold import comprar_y_mantener
from bot.cartera import CARTERA_CLAUDE, CARTERA_SOLO
from bot.config import Config
from bot.datos.historico import cargar_velas
from bot.db.modelos import LlamadaClaude, Operacion, PuntoCapital
from panel.datos import metricas

MIN_OPERACIONES = 30


def periodo_paper(s: Session) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    ini, fin = s.execute(select(func.min(PuntoCapital.ts_ms), func.max(PuntoCapital.ts_ms))).one()
    if ini is None:
        return None
    return pd.Timestamp(ini, unit="ms", tz="UTC"), pd.Timestamp(fin, unit="ms", tz="UTC")


def r_de(s: Session, cartera: str) -> pd.Series:
    return pd.Series([o.r_multiple for o in s.scalars(select(Operacion).where(
        Operacion.cartera == cartera, Operacion.estado == "cerrada"))], dtype=float)


def generar(s: Session, config: Config) -> str:
    per = periodo_paper(s)
    if per is None:
        return "# Informe comparativo\n\nAún no hay datos de paper trading. Arranca el bot (`python scripts/bot.py`).\n"
    ini, fin = per
    dias = (fin - ini).total_seconds() / 86400
    m = {c: metricas(s, c) for c in (CARTERA_CLAUDE, CARTERA_SOLO)}
    gasto = float(s.scalar(select(func.sum(LlamadaClaude.costo_usd)).where(
        LlamadaClaude.ts_ms >= int(ini.timestamp() * 1000))) or 0.0)
    capital = config.capital.simulado_usd
    velas = {p: df for p in config.pares
             if not (df := cargar_velas(s, config.exchange.nombre, p, config.temporalidad)).empty}
    bh = comprar_y_mantener(velas, capital, ini, fin, config.backtest.comision_pct, config.backtest.slippage_pct) \
        if velas else {"retorno_pct": float("nan"), "max_drawdown_pct": float("nan")}
    pnl_c = m[CARTERA_CLAUDE]["pnl_neto_usd"] if "pnl_neto_usd" in m[CARTERA_CLAUDE] else 0.0
    pnl_s = m[CARTERA_SOLO]["pnl_neto_usd"] if "pnl_neto_usd" in m[CARTERA_SOLO] else 0.0
    neto_c = pnl_c - gasto
    rc, rs = r_de(s, CARTERA_CLAUDE), r_de(s, CARTERA_SOLO)
    p = p_valor_permutacion(rc.to_numpy(), rs.to_numpy(), "mejor") if len(rc) >= 5 and len(rs) >= 5 else None

    conclusiones = []
    if dias < 14:
        conclusiones.append(f"⚠️ Solo hay {dias:.1f} días de paper trading: el mínimo acordado antes de pensar en real es 14.")
    if min(len(rc), len(rs)) < MIN_OPERACIONES:
        conclusiones.append(f"⚠️ Muestra pequeña ({len(rc)} operaciones con Claude y {len(rs)} sin Claude; se necesitan al "
                            f"menos {MIN_OPERACIONES} en cada una). Cualquier diferencia puede ser suerte.")
    if neto_c > pnl_s:
        conclusiones.append(f"Con Claude, descontando su costo ({gasto:.2f} USD), se obtuvo {neto_c:+.2f} USD frente a "
                            f"{pnl_s:+.2f} USD sin Claude.")
        if p is not None and p < 0.05 and min(len(rc), len(rs)) >= MIN_OPERACIONES:
            conclusiones.append(f"La mejora por operación es estadísticamente significativa (p = {p:.3f}).")
        else:
            conclusiones.append("Pero todavía NO está demostrado que la mejora no sea casualidad"
                                + (f" (p = {p:.3f})." if p is not None else "."))
    else:
        conclusiones.append(f"❌ Claude NO mejoró los resultados: con Claude y descontando su costo ({gasto:.2f} USD) se "
                            f"obtuvo {neto_c:+.2f} USD, frente a {pnl_s:+.2f} USD de la estrategia técnica sola. "
                            "Si esto se mantiene, lo sensato es operar sin Claude o cambiar su papel.")
    for nombre, pnl in (("técnico + Claude", neto_c), ("técnico solo", pnl_s)):
        ret = pnl / capital * 100
        if not pd.isna(bh["retorno_pct"]) and ret < bh["retorno_pct"]:
            conclusiones.append(f"La cartera {nombre} ({ret:+.2f}%) rindió menos que comprar y mantener "
                                f"({bh['retorno_pct']:+.2f}%), aunque con otro nivel de riesgo (caída máxima de "
                                f"comprar y mantener: {bh['max_drawdown_pct']:.1f}%).")
    if max(pnl_c, pnl_s) <= 0:
        conclusiones.append("❌ Ninguna de las dos carteras ganó dinero en el periodo: no hay base para pasar a real.")

    def f(x, d=2, suf=""):
        return "—" if x is None or pd.isna(x) else f"{x:,.{d}f}{suf}"

    filas = [
        ("Operaciones cerradas", len(rc), len(rs), "—"),
        ("Resultado (USD)", f(pnl_c), f(pnl_s), f(bh["retorno_pct"] / 100 * capital if not pd.isna(bh["retorno_pct"]) else None)),
        ("Costo de Claude (USD)", f(gasto), "0.00", "0.00"),
        ("Resultado neto de costos de IA (USD)", f(neto_c), f(pnl_s), "—"),
        ("Retorno neto", f(neto_c / capital * 100, 2, "%"), f(pnl_s / capital * 100, 2, "%"), f(bh["retorno_pct"], 2, "%")),
        ("Caída máxima", f(m[CARTERA_CLAUDE]["max_drawdown_pct"], 1, "%"), f(m[CARTERA_SOLO]["max_drawdown_pct"], 1, "%"),
         f(bh["max_drawdown_pct"], 1, "%")),
        ("Tasa de acierto", f(m[CARTERA_CLAUDE].get("tasa_acierto_pct"), 1, "%"), f(m[CARTERA_SOLO].get("tasa_acierto_pct"), 1, "%"), "—"),
        ("Factor de beneficio", f(m[CARTERA_CLAUDE].get("factor_beneficio")), f(m[CARTERA_SOLO].get("factor_beneficio")), "—"),
        ("R medio por operación", f(rc.mean() if len(rc) else None, 3), f(rs.mean() if len(rs) else None, 3), "—"),
        ("Sharpe (anual)", f(m[CARTERA_CLAUDE].get("sharpe")), f(m[CARTERA_SOLO].get("sharpe")), "—"),
        ("Peor racha", m[CARTERA_CLAUDE].get("peor_racha", 0), m[CARTERA_SOLO].get("peor_racha", 0), "—"),
    ]
    L = ["# Informe comparativo del paper trading", "",
         f"Periodo: {ini:%Y-%m-%d %H:%M} a {fin:%Y-%m-%d %H:%M} UTC ({dias:.1f} días). Capital simulado: {capital:.0f} USD por cartera.",
         "", "## Conclusiones", "", *[f"- {c}" for c in conclusiones], "",
         "## Comparación", "", "| Métrica | Técnico + Claude | Técnico solo | Comprar y mantener |", "|---|---|---|---|",
         *[f"| {a} | {b} | {c} | {d} |" for a, b, c, d in filas], "",
         "## Cómo leer esto", "",
         "- Las dos carteras recibieron exactamente las mismas señales y precios; la única diferencia es el filtro de Claude.",
         "- \"Resultado neto de costos de IA\" resta a la cartera con Claude lo que se pagó a la API en el periodo.",
         "- Comprar y mantener reparte el capital entre los pares al inicio del periodo y no hace nada más; es la referencia"
         " mínima que una estrategia activa debería superar para justificar su riesgo y su trabajo.",
         "- La comparación con el backtest (estrategia técnica sola en años de histórico) está en `reportes/backtest_*.md`.",
         "- Resultados pasados, y más aún simulados, no garantizan resultados futuros."]
    return "\n".join(L) + "\n"
