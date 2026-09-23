"""Capa de riesgo: reglas FIJAS escritas en código que deciden al final.

Claude propone; esta capa dispone. Si una propuesta viola cualquier regla, se bloquea y se registra el motivo.
Cerrar posiciones siempre está permitido (reduce riesgo); estas reglas solo gobiernan las APERTURAS.

Reglas de apertura (se evalúan todas y se informan todos los incumplimientos):
  R1  El bot/cartera debe estar ACTIVO (no pausado por pérdida diaria, no detenido por caída máxima o emergencia).
  R2  La pérdida del día no alcanza el límite diario.
  R3  La caída desde el máximo de capital no alcanza el límite de drawdown.
  R4  Queda tiempo suficiente antes del cierre diario obligatorio.
  R5  No se supera el máximo de posiciones abiertas.
  R6  No hay ya una posición abierta en ese par.
  R7  Stop loss obligatorio, en el lado correcto del precio; take profit también en su lado.
  R8  La distancia al stop está dentro de [mínimo, máximo] %.
  R9  Si la operación pasa por Claude: su confianza es >= el mínimo y su respuesta fue válida y coherente.
  R10 El tamaño es viable: la pérdida en el stop no supera el riesgo permitido (8 USD o el 1% del capital, lo que
      sea menor; Claude solo puede reducirlo) y la exposición total no supera capital x apalancamiento.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from bot.config import Config
from bot.dimensionamiento import Tamano, calcular_tamano, riesgo_permitido_usd

ACTIVO, PAUSADO, DETENIDO = "activo", "pausado", "detenido"


@dataclass
class Propuesta:
    cartera: str
    par: str
    direccion: str                  # largo | corto
    precio_entrada: float           # precio estimado de entrada (el actual)
    stop_loss: float
    take_profit: float
    origen: str                     # "senal_tecnica" | "senal_tecnica+claude"
    confianza: float | None = None  # None si no pasó por Claude
    tamano_pct: float = 100.0       # % del riesgo permitido (Claude puede reducirlo)
    problemas_previos: list[str] = field(default_factory=list)  # incoherencias o fallos de Claude

    @property
    def usa_claude(self) -> bool:
        return "claude" in self.origen


@dataclass
class EstadoRiesgo:
    estado: str
    motivo: str
    capital: float                  # marcado a mercado
    pico: float
    capital_inicio_dia: float
    pnl_dia: float                  # realizado hoy
    posiciones: dict[str, float]    # par -> nocional abierto


@dataclass
class Veredicto:
    permitido: bool
    bloqueos: list[str]
    reglas_cumplidas: list[str]
    tamano: Tamano | None = None


def umbral_confianza(config: Config) -> float:
    """El más exigente entre la regla fija y el parámetro aprendido (que nunca baja de su mínimo)."""
    return max(config.riesgo.confianza_min_claude, config.estrategia.confianza_min.valor)


def drawdown_pct(capital: float, pico: float) -> float:
    return (pico - capital) / pico * 100 if pico > 0 else 0.0


def evaluar_apertura(p: Propuesta, e: EstadoRiesgo, config: Config, ahora: pd.Timestamp) -> Veredicto:
    r = config.riesgo
    bloqueos: list[str] = []
    ok: list[str] = []

    def regla(codigo: str, cumple: bool, texto_ok: str, texto_bloqueo: str) -> None:
        (ok if cumple else bloqueos).append(f"{codigo}: {texto_ok if cumple else texto_bloqueo}")

    regla("R1", e.estado == ACTIVO, "bot activo", f"bot {e.estado} ({e.motivo})")
    limite_dia = -r.perdida_diaria_max_pct / 100 * e.capital_inicio_dia
    regla("R2", e.pnl_dia > limite_dia, f"pérdida del día {e.pnl_dia:.2f} USD dentro del límite ({limite_dia:.2f})",
          f"pérdida del día {e.pnl_dia:.2f} USD alcanzó el límite de {limite_dia:.2f} USD")
    dd = drawdown_pct(e.capital, e.pico)
    regla("R3", dd < r.drawdown_max_pct, f"caída desde el máximo {dd:.1f}% < {r.drawdown_max_pct}%",
          f"caída desde el máximo {dd:.1f}% alcanzó el límite de {r.drawdown_max_pct}%")

    hh, mm = map(int, r.hora_cierre_diario_utc.split(":"))
    minutos_restantes = hh * 60 + mm - (ahora.hour * 60 + ahora.minute)
    horas_min = config.senales.horas_minimas_antes_cierre
    regla("R4", minutos_restantes >= horas_min * 60, f"quedan {minutos_restantes} min antes del cierre diario",
          f"faltan {minutos_restantes} min para el cierre de las {r.hora_cierre_diario_utc} UTC (mínimo {horas_min} h)")

    regla("R5", len(e.posiciones) < r.max_posiciones, f"{len(e.posiciones)}/{r.max_posiciones} posiciones",
          f"ya hay {len(e.posiciones)} posiciones abiertas (máximo {r.max_posiciones})")
    regla("R6", p.par not in e.posiciones, "sin posición previa en el par", f"ya hay una posición abierta en {p.par}")

    d = 1 if p.direccion == "largo" else -1
    lados_ok = (p.stop_loss > 0 and (p.precio_entrada - p.stop_loss) * d > 0 and (p.take_profit - p.precio_entrada) * d > 0)
    regla("R7", lados_ok, f"stop loss {p.stop_loss} y objetivo {p.take_profit} en el lado correcto",
          f"stop loss {p.stop_loss} / objetivo {p.take_profit} inválidos para un {p.direccion} a {p.precio_entrada}")

    dist_pct = abs(p.precio_entrada - p.stop_loss) / p.precio_entrada * 100 if p.precio_entrada > 0 else 0.0
    ej = config.ejecucion
    regla("R8", ej.distancia_stop_min_pct <= dist_pct <= ej.distancia_stop_max_pct,
          f"stop a {dist_pct:.2f}% del precio", f"stop a {dist_pct:.2f}% del precio, fuera de "
          f"[{ej.distancia_stop_min_pct}%, {ej.distancia_stop_max_pct}%]")

    if p.usa_claude:
        umbral = umbral_confianza(config)
        if p.problemas_previos:
            bloqueos.append("R9: " + "; ".join(p.problemas_previos))
        elif p.confianza is None or p.confianza < umbral:
            bloqueos.append(f"R9: confianza de Claude {p.confianza} menor que el mínimo {umbral}")
        else:
            ok.append(f"R9: confianza de Claude {p.confianza:.2f} >= {umbral}")

    tamano = None
    if lados_ok:
        riesgo = riesgo_permitido_usd(e.capital, r.sl_usd_por_operacion, r.riesgo_max_pct_operacion)
        riesgo *= max(0.0, min(p.tamano_pct, 100.0)) / 100
        exposicion_libre = e.capital * r.apalancamiento - sum(e.posiciones.values())
        nocional_max = min(e.capital * r.apalancamiento / r.max_posiciones, exposicion_libre)
        tamano = calcular_tamano(
            p.precio_entrada, abs(p.precio_entrada - p.stop_loss), riesgo, config.backtest.comision_pct,
            config.backtest.slippage_pct, nocional_max=nocional_max, nocional_min=config.backtest.nocional_min_usd,
        )
        regla("R10", tamano is not None,
              f"riesgo {tamano.riesgo_usd:.2f} USD (máx. {riesgo:.2f}), nocional {tamano.nocional:.2f} USD"
              if tamano else "", f"tamaño no viable (riesgo {riesgo:.2f} USD, nocional máximo {nocional_max:.2f} USD)")
    else:
        bloqueos.append("R10: sin stop válido no se puede calcular el tamaño")

    return Veredicto(permitido=not bloqueos, bloqueos=bloqueos, reglas_cumplidas=ok, tamano=tamano if not bloqueos else None)


def transicion_por_limites(e: EstadoRiesgo, config: Config) -> tuple[str, str] | None:
    """¿Hay que pausar (pérdida diaria) o detener (drawdown)? Devuelve (nuevo_estado, motivo) o None."""
    r = config.riesgo
    dd = drawdown_pct(e.capital, e.pico)
    if e.estado != DETENIDO and dd >= r.drawdown_max_pct:
        return DETENIDO, (f"Caída de {dd:.1f}% desde el máximo ({e.pico:.2f} -> {e.capital:.2f} USD). "
                          "El bot se detiene hasta revisión humana.")
    if e.estado == ACTIVO and e.pnl_dia <= -r.perdida_diaria_max_pct / 100 * e.capital_inicio_dia:
        return PAUSADO, f"Pérdida del día {e.pnl_dia:.2f} USD alcanzó el {r.perdida_diaria_max_pct}%. Pausa hasta mañana."
    return None
