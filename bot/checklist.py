"""Checklist ANTES de operar con dinero real.

Aunque todo salga OK, el bot NO opera con dinero real: el modo real sigue bloqueado en el código hasta que el dueño
lo autorice explícitamente y se complete la fase de ejecución real (ver README → "Paso a real").
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.cartera import CARTERA_CLAUDE, CARTERA_SOLO
from bot.config import RAIZ, Config, Secretos
from bot.db.modelos import EstadoCartera, PuntoCapital
from bot.informe_final import MIN_OPERACIONES, periodo_paper, r_de

OK, FALLA, REVISAR, MANUAL = "✅ OK", "❌ FALLA", "⚠️ REVISAR", "✋ MANUAL"


@dataclass
class Punto:
    nombre: str
    estado: str
    detalle: str


def dias_sin_interrupcion(s: Session, hueco_max_h: float = 3.0) -> float:
    """Tramo continuo más largo del paper trading (la curva de capital se guarda cada hora)."""
    ts = pd.Series([t for (t,) in s.execute(select(PuntoCapital.ts_ms).where(
        PuntoCapital.cartera == CARTERA_SOLO).order_by(PuntoCapital.ts_ms))], dtype="int64")
    if len(ts) < 2:
        return 0.0
    cortes = ts.diff() > hueco_max_h * 3_600_000
    mejor = 0.0
    inicio = ts.iloc[0]
    for i in range(1, len(ts)):
        if cortes.iloc[i]:
            mejor = max(mejor, (ts.iloc[i - 1] - inicio) / 86_400_000)
            inicio = ts.iloc[i]
    return max(mejor, (ts.iloc[-1] - inicio) / 86_400_000)


def ultimo_backtest() -> tuple[str | None, str]:
    informes = sorted((RAIZ / "reportes").glob("backtest_*.md"))
    informes = [p for p in informes if not p.name.endswith(("_operaciones.csv", "_curvas.csv"))]
    if not informes:
        return None, ""
    return informes[-1].name, informes[-1].read_text(encoding="utf-8")


def evaluar(s: Session, config: Config, secretos: Secretos, correr_pruebas: bool = True) -> list[Punto]:
    puntos: list[Punto] = []

    if correr_pruebas:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=RAIZ, capture_output=True, text=True)
        ultima = (r.stdout.strip().splitlines() or ["?"])[-1]
        puntos.append(Punto("Todas las pruebas automáticas pasan", OK if r.returncode == 0 else FALLA, ultima))

    nombre, texto = ultimo_backtest()
    if nombre is None:
        puntos.append(Punto("Backtest de la estrategia", FALLA, "No hay informe: ejecuta python scripts/backtest.py"))
    else:
        rentable = "NO fue rentable" not in texto
        detenido = "DETENIDO" in texto
        estado = OK if rentable and not detenido else FALLA
        puntos.append(Punto("Backtest fuera de muestra rentable y sin parada por caída máxima", estado,
                            f"{nombre}: " + ("rentable" if rentable else "NO rentable") + (", se detuvo" if detenido else "")))

    per = periodo_paper(s)
    dias = (per[1] - per[0]).total_seconds() / 86400 if per else 0.0
    puntos.append(Punto("Al menos 14 días de paper trading", OK if dias >= 14 else FALLA, f"{dias:.1f} días"))
    continuo = dias_sin_interrupcion(s)
    puntos.append(Punto("Una semana seguida funcionando sin intervención (sin huecos > 3 h)",
                        OK if continuo >= 7 else FALLA, f"tramo continuo más largo: {continuo:.1f} días"))

    for cartera in (CARTERA_CLAUDE, CARTERA_SOLO):
        r = r_de(s, cartera)
        est = s.get(EstadoCartera, cartera)
        n_ok = len(r) >= MIN_OPERACIONES
        positivo = len(r) > 0 and r.mean() > 0
        detenida = est is not None and est.estado == "detenido"
        estado = FALLA if detenida or (n_ok and not positivo) else (OK if n_ok and positivo else REVISAR)
        puntos.append(Punto(f"Paper trading aceptable ({cartera})", estado,
                            f"{len(r)} operaciones, R medio {r.mean() if len(r) else 0:+.3f}"
                            + (", CARTERA DETENIDA por caída máxima o emergencia" if detenida else "")
                            + ("" if n_ok else f" (se necesitan ≥ {MIN_OPERACIONES})")))

    datos = config.model_dump()
    datos["modo"] = "real"
    datos["exchange"]["demo"] = False
    real = Config.model_validate(datos)
    problemas = real.problemas_de_riesgo()
    puntos.append(Punto(f"Reglas de riesgo coherentes con el capital real ({config.capital.real_usd:g} USD)",
                        FALLA if problemas else OK, " ".join(problemas) or
                        f"SL {config.riesgo.sl_usd_por_operacion} USD ≤ {config.riesgo.riesgo_max_pct_operacion}% del capital"))

    puntos.append(Punto("Avisos por Telegram configurados", OK if config.telegram.habilitado and secretos.telegram_bot_token
                        and secretos.telegram_chat_id else FALLA, "necesarios para enterarte de paradas y emergencias"))
    puntos.append(Punto("Presupuesto de Claude con límite también en la consola de Anthropic", MANUAL,
                        "console.anthropic.com → Limits: pon un tope mensual como segunda barrera"))
    puntos.append(Punto("Claves del exchange REAL sin permiso de retiro", MANUAL,
                        "crea claves nuevas solo con lectura y trading; verifícalo con python scripts/verificar_conexion.py"))
    puntos.append(Punto("Botón de emergencia probado en paper", MANUAL,
                        "ejecuta python scripts/emergencia.py con posiciones abiertas y comprueba que cierra todo"))
    puntos.append(Punto("Revisaste el diario de varias operaciones y entiendes por qué se abrieron", MANUAL,
                        "panel → Detalle de operación"))
    puntos.append(Punto("Ejecución real implementada y revisada", FALLA,
                        "Por diseño el bot solo opera en paper. Pasar a real requiere una fase adicional con tu "
                        "autorización explícita: activar el broker real, probar con el capital mínimo y revisar juntos."))
    return puntos


def texto(puntos: list[Punto]) -> str:
    L = ["# Checklist antes de pasar a dinero real", "", "| Punto | Estado | Detalle |", "|---|---|---|"]
    L += [f"| {p.nombre} | {p.estado} | {re.sub(r'[|]', '/', p.detalle)} |" for p in puntos]
    fallas = sum(p.estado == FALLA for p in puntos)
    L += ["", f"**{fallas} punto(s) fallan.** " + ("No se puede pasar a real." if fallas else
          "Los automáticos pasan: revisa los manuales y decide tú; el modo real sigue bloqueado en el código.")]
    return "\n".join(L) + "\n"
