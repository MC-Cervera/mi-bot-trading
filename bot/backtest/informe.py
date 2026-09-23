"""Informe en Markdown (español, lenguaje claro) de un backtest walk-forward."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from bot.backtest.metricas import resumen_por


def _f(x, dec=2, sufijo=""):
    if x is None or (isinstance(x, float) and (math.isnan(x))):
        return "—"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    return f"{x:,.{dec}f}{sufijo}"


def _tabla(df: pd.DataFrame) -> str:
    if df.empty:
        return "_(sin datos)_\n"
    cols = [NOMBRES_COLUMNAS.get(str(c), str(c)) for c in df.columns]
    lineas = ["| " + " | ".join([df.index.name or ""] + cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for idx, *valores in df.itertuples(name=None):
        celdas = [_f(float(v)) if isinstance(v, (float, np.floating)) else str(v) for v in valores]
        lineas.append("| " + " | ".join([str(idx)] + celdas) + " |")
    return "\n".join(lineas) + "\n"


NOMBRES_COLUMNAS = {"operaciones": "Operaciones", "tasa_acierto_pct": "Acierto %", "pnl_neto_usd": "Resultado neto (USD)",
                    "veces": "Veces"}

FILAS_METRICAS = [
    ("Operaciones", "operaciones", 0, ""),
    ("Retorno", "retorno_pct", 2, "%"),
    ("Capital final (USD)", "capital_final", 2, ""),
    ("Caída máxima (drawdown)", "max_drawdown_pct", 2, "%"),
    ("Tasa de acierto", "tasa_acierto_pct", 1, "%"),
    ("Factor de beneficio", "factor_beneficio", 2, ""),
    ("Ganancia media por operación (USD)", "expectativa_usd", 2, ""),
    ("Ganancia media en R (múltiplos del riesgo)", "expectativa_r", 3, ""),
    ("Ratio de Sharpe (anual)", "sharpe", 2, ""),
    ("Peor racha (pérdidas seguidas)", "peor_racha", 0, ""),
    ("Comisiones pagadas (USD)", "comisiones_usd", 2, ""),
    ("Funding pagado (USD)", "funding_usd", 2, ""),
]


def conclusiones(m_opt: dict, m_def: dict, bh: dict, parada=None) -> list[str]:
    """Conclusiones automáticas y honestas. No maquillan resultados."""
    c = []
    if parada is not None:
        c.append(f"🛑 El bot se habría DETENIDO el {parada:%Y-%m-%d} al alcanzar la caída máxima permitida y no habría "
                 "vuelto a operar hasta una revisión humana. Los periodos siguientes muestran 0 operaciones por eso.")
    n = m_opt["operaciones"]
    if n < 100:
        c.append(f"⚠️ Solo {n} operaciones fuera de muestra: la muestra es pequeña y los resultados pueden ser suerte.")
    rentable = m_opt["retorno_pct"] > 0 and m_opt.get("factor_beneficio", 0) > 1
    if rentable:
        c.append(f"La estrategia técnica optimizada fue rentable fuera de muestra ({m_opt['retorno_pct']:+.2f}%), "
                 f"con factor de beneficio {_f(m_opt['factor_beneficio'])}.")
    else:
        c.append(f"❌ La estrategia técnica sola NO fue rentable fuera de muestra ({m_opt['retorno_pct']:+.2f}%). "
                 "No debe pasar a real en su forma actual.")
    if m_opt["retorno_pct"] < m_def["retorno_pct"]:
        c.append("La optimización en entrenamiento empeoró el resultado frente a los parámetros por defecto: señal de "
                 "sobreajuste. Conviene quedarse con los parámetros por defecto o reducir la rejilla.")
    if m_opt["retorno_pct"] > bh["retorno_pct"]:
        c.append(f"Superó a comprar y mantener ({bh['retorno_pct']:+.2f}%) en el mismo periodo.")
    else:
        c.append(f"No superó a comprar y mantener ({bh['retorno_pct']:+.2f}%) en el mismo periodo. Ojo: comprar y "
                 f"mantener tuvo una caída máxima de {bh['max_drawdown_pct']:.1f}% frente a "
                 f"{m_opt['max_drawdown_pct']:.1f}% de la estrategia; son riesgos muy distintos.")
    costos = m_opt.get("comisiones_usd", 0) + m_opt.get("funding_usd", 0)
    bruto = m_opt.get("pnl_neto_usd", 0) + costos
    if bruto > 0 and costos > 0.5 * bruto:
        c.append(f"Los costos ({costos:.2f} USD) se comieron más de la mitad de la ganancia bruta ({bruto:.2f} USD).")
    return c


def generar_informe(datos: dict) -> str:
    m_opt, m_def, bh, bh_btc = datos["m_opt"], datos["m_def"], datos["bh"], datos["bh_btc"]
    ops = datos["ops_opt"]
    L = [
        "# Informe de backtesting — estrategia técnica sola (sin Claude)",
        "",
        f"Generado: {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC",
        "",
        "## Qué se probó",
        "",
        f"- Pares: {', '.join(datos['pares'])}",
        f"- Datos: {datos['desde']:%Y-%m-%d} a {datos['hasta']:%Y-%m-%d} (velas de {datos['temporalidad']})",
        f"- Periodo evaluado fuera de muestra: {datos['oos_desde']:%Y-%m-%d} a {datos['oos_hasta']:%Y-%m-%d} "
        f"({len(datos['ventanas'])} ventanas de prueba)",
        f"- Capital inicial: {datos['capital']:.0f} USD. Riesgo por operación: {datos['pb'].sl_usd} USD. "
        f"Máx. {datos['pb'].max_posiciones} posiciones. Apalancamiento {datos['pb'].apalancamiento}x.",
        f"- Costos: comisión {datos['pb'].comision_pct}% por lado, deslizamiento {datos['pb'].slippage_pct}%, "
        f"funding {datos['pb'].funding_pct_8h}% cada 8 h (siempre como costo).",
        "- Cierre obligatorio de todo a las 23:00 UTC.",
        "",
        "**Cómo leer esto:** en cada ventana, el bot eligió sus parámetros mirando SOLO los 6 meses anteriores y luego se "
        "midió en los 2 meses siguientes, que no había visto. Solo se muestran esos meses \"no vistos\": es lo más "
        "parecido a operar en el futuro.",
        "",
        "## Conclusiones",
        "",
        *[f"- {c}" for c in conclusiones(m_opt, m_def, bh, datos.get("parada"))],
        "",
        "## Comparación fuera de muestra",
        "",
        "| Métrica | Técnica optimizada (walk-forward) | Técnica con parámetros por defecto | Comprar y mantener (15 pares) | Comprar y mantener (solo BTC) |",
        "|---|---|---|---|---|",
    ]
    for nombre, clave, dec, suf in FILAS_METRICAS:
        fila = [_f(m_opt.get(clave), dec, suf), _f(m_def.get(clave), dec, suf)]
        fila += [_f(bh.get(clave), dec, suf) if clave in ("retorno_pct", "max_drawdown_pct") else "—",
                 _f(bh_btc.get(clave), dec, suf) if clave in ("retorno_pct", "max_drawdown_pct") else "—"]
        L.append(f"| {nombre} | " + " | ".join(fila) + " |")
    L += ["", "## Ventanas walk-forward", "",
          "| Prueba | Parámetros elegidos (EMA, volumen, SL×ATR, TP) | Ops. entrenamiento | Ops. prueba | Retorno prueba | Retorno por defecto |",
          "|---|---|---|---|---|---|"]
    for r in datos["ventanas"]:
        e = r.elegidos
        rp = (r.prueba.capital_final / r.prueba.capital_inicial - 1) * 100
        rd = (r.prueba_por_defecto.capital_final / r.prueba_por_defecto.capital_inicial - 1) * 100
        estado = " (bot detenido)" if r.prueba.detenido and r.prueba.operaciones.empty else ""
        L.append(
            f"| {r.ventana.inicio_prueba:%Y-%m-%d} → {r.ventana.fin_prueba:%Y-%m-%d} | "
            f"{e.indicadores.ema_rapida}/{e.indicadores.ema_lenta}, {e.multiplicador_volumen}x, {e.atr_mult_sl}, "
            f"{e.ratio_tp}:1 | {r.operaciones_entrenamiento} | {len(r.prueba.operaciones)}{estado} | {rp:+.2f}% | {rd:+.2f}% |"
        )
    if not ops.empty:
        ops = ops.copy()
        ops["mes"] = pd.to_datetime(ops["ts_salida"]).dt.strftime("%Y-%m")
        L += ["", "## Resultados por par (fuera de muestra)", "", _tabla(resumen_por(ops, "par").rename_axis("Par")),
              "## Por dirección", "", _tabla(resumen_por(ops, "direccion").rename_axis("Dirección")),
              "## Por motivo de salida", "", _tabla(resumen_por(ops, "motivo_salida").rename_axis("Motivo")),
              "## Por mes", "", _tabla(resumen_por(ops, "mes").sort_index().rename_axis("Mes"))]
    ev = pd.DataFrame(datos["eventos"])
    if not ev.empty:
        nombres = {"parada_drawdown": "Parada por caída máxima", "pausa_diaria": "Pausa por pérdida diaria",
                   "senal_omitida": "Señal no operada (máx. posiciones o tamaño no viable)"}
        tabla = ev.assign(tipo=ev["tipo"].map(nombres).fillna(ev["tipo"])).groupby("tipo").size().to_frame("veces")
        L += ["## Eventos de riesgo", "", _tabla(tabla.rename_axis("Evento"))]
    L += ["", "## Limitaciones (léelas)", "",
          "- Los resultados pasados no garantizan resultados futuros.",
          "- Se usan velas de 1 h: dentro de una vela no se sabe si tocó antes el stop o el objetivo; se asume el stop.",
          "- El funding real puede ser a favor o en contra; aquí siempre se cobra.",
          "- El mínimo de orden real de Binance varía por par (a veces más de 5 USD); se validará en la Fase 5.",
          "- No incluye noticias ni a Claude: es la línea base contra la que se medirá a Claude en paper trading."]
    return "\n".join(L) + "\n"
