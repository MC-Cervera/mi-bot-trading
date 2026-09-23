"""Diario de trading: por qué se abrió cada operación, cómo terminó y qué se puede aprender de ella.

Los textos se escriben en lenguaje claro para que una persona que está aprendiendo pueda seguirlos.
El análisis posterior es automático (reglas en código, sin costo de API).
"""
from __future__ import annotations

import json

import pandas as pd

from bot.db.modelos import Noticia, Operacion


def _fecha(ms: int | None) -> str:
    return "—" if ms is None else pd.Timestamp(ms, unit="ms", tz="UTC").strftime("%Y-%m-%d %H:%M UTC")


def texto_entrada(*, explicacion_senal: str, decision=None, noticias: list[Noticia] | None = None,
                  reglas_cumplidas: list[str], precio: float, stop: float, objetivo: float, riesgo_usd: float,
                  nocional: float, cartera: str) -> str:
    L = [f"## Entrada ({cartera})", "", "### Qué vio el análisis técnico", explicacion_senal, ""]
    if decision is not None:
        L += ["### Qué dijo Claude",
              f"Decisión: **{decision.accion}** con confianza {decision.confianza:.2f} "
              f"(usar el {decision.tamano_sugerido_pct:.0f}% del riesgo permitido).",
              f"Razonamiento: {decision.razonamiento}"]
        if decision.factores_a_favor:
            L += ["A favor:"] + [f"- {f}" for f in decision.factores_a_favor]
        if decision.factores_en_contra:
            L += ["En contra / riesgos:"] + [f"- {f}" for f in decision.factores_en_contra]
        if decision.hipotesis_que_aplica:
            L.append(f"Lecciones aplicadas: {', '.join(decision.hipotesis_que_aplica)}")
        L.append("")
    else:
        L += ["### Sin Claude", "Cartera de control: se opera la señal técnica tal cual, sin filtro de IA.", ""]
    if noticias:
        L += ["### Noticias consideradas"] + [
            f"- {n.titulo} (sentimiento {n.sentimiento}, impacto {n.impacto})" for n in noticias[:8]] + [""]
    L += ["### Por qué la capa de riesgo la permitió"] + [f"- {r}" for r in reglas_cumplidas] + [""]
    L += ["### Plan", f"Entrada ≈ {precio:.6g} · stop loss {stop:.6g} · objetivo {objetivo:.6g} · "
          f"pérdida máxima prevista {riesgo_usd:.2f} USD · tamaño {nocional:.2f} USD."]
    return "\n".join(L)


MOTIVOS = {
    "stop_loss": "saltó el stop loss", "take_profit": "alcanzó el objetivo (take profit)",
    "cierre_diario": "cierre obligatorio de las 23:00 UTC", "claude_noticia": "Claude pidió cerrar por una noticia",
    "emergencia": "botón de emergencia", "drawdown_maximo": "se alcanzó la caída máxima permitida",
}


def texto_salida(op: Operacion) -> str:
    return (f"## Salida\nSe cerró el {_fecha(op.ts_salida_ms)} porque {MOTIVOS.get(op.motivo_salida, op.motivo_salida)}, "
            f"a {op.precio_salida:.6g}. Resultado neto: {op.pnl_neto:+.2f} USD ({op.r_multiple:+.2f}R), "
            f"de los cuales {op.comisiones:.2f} USD fueron comisiones y {op.funding:.2f} USD funding.")


def analisis_posterior(op: Operacion) -> str:
    """Qué pasó realmente y qué hipótesis sugiere (para el sistema de aprendizaje y para la persona)."""
    d = 1 if op.direccion == "largo" else -1
    riesgo_precio = abs(op.precio_entrada - op.stop_loss) or 1e-12
    mfe = ((op.max_favorable or op.precio_entrada) - op.precio_entrada) * d / riesgo_precio
    mae = ((op.max_adverso or op.precio_entrada) - op.precio_entrada) * d / riesgo_precio
    horas = ((op.ts_salida_ms or op.ts_entrada_ms) - op.ts_entrada_ms) / 3_600_000
    L = ["## Análisis posterior",
         f"- Duración: {horas:.1f} h.",
         f"- Lo mejor que llegó a ir: {mfe:+.2f}R. Lo peor: {mae:+.2f}R (R = distancia al stop).",
         f"- Costos: {op.comisiones + op.funding:.2f} USD ({(op.comisiones + op.funding) / op.riesgo_usd * 100:.0f}% del riesgo)."
         if op.riesgo_usd else "- Costos: —"]
    obs = []
    if op.pnl_neto is not None and op.pnl_neto < 0 and mfe >= 1:
        obs.append("Estuvo al menos 1R a favor y terminó en pérdida: un stop a punto de equilibrio o un objetivo más "
                   "cercano quizá habría ayudado (hipótesis a validar con muchas operaciones, no con una).")
    if op.motivo_salida == "stop_loss" and mfe < 0.2:
        obs.append("El precio fue en contra casi desde el inicio: la entrada no tuvo seguimiento. Revisar si las "
                   "confirmaciones (volumen, RSI, noticias) eran débiles.")
    if op.motivo_salida == "cierre_diario":
        obs.append("Terminó por horario, no por precio: la idea no llegó a resolverse en el día.")
    if op.motivo_salida == "take_profit" and mae <= -0.8:
        obs.append("Llegó al objetivo pero estuvo muy cerca de tocar el stop: ganó con poco margen.")
    if op.confianza_claude is not None:
        acierto = (op.pnl_neto or 0) > 0
        obs.append(f"Claude dio confianza {op.confianza_claude:.2f} y la operación {'ganó' if acierto else 'perdió'}: "
                   "este dato alimenta la calibración de su confianza.")
    if op.comisiones + op.funding > 0.3 * abs(op.pnl_neto or 0) and op.pnl_neto:
        obs.append("Los costos fueron una parte grande del resultado.")
    L += ["- " + o for o in obs] or ["- Sin observaciones destacables."]
    return "\n".join(L)


def lecciones_json(decision) -> str:
    return json.dumps(decision.hipotesis_que_aplica if decision is not None else [])
