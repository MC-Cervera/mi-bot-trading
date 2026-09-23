"""Cálculo del tamaño de la posición a partir del stop loss.

Idea: se decide primero DÓNDE va el stop (análisis técnico) y luego CUÁNTO comprar para que, si salta,
la pérdida total (incluyendo comisiones y deslizamiento) no supere el riesgo permitido en USD.
Lo usan el backtest y, en la Fase 5, la capa de riesgo.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tamano:
    cantidad: float
    nocional: float
    riesgo_usd: float        # pérdida estimada si salta el stop (incluye costos)
    limitado_por: str        # "riesgo" | "nocional_maximo"


def riesgo_permitido_usd(capital: float, sl_usd: float, riesgo_max_pct: float) -> float:
    """El SL fijo en USD, pero nunca más del % máximo del capital actual (si la cuenta baja, se arriesga menos)."""
    return min(sl_usd, capital * riesgo_max_pct / 100)


def calcular_tamano(
    precio_entrada: float,
    distancia_stop: float,
    riesgo_usd: float,
    comision_pct: float,
    slippage_pct: float,
    nocional_max: float,
    nocional_min: float,
) -> Tamano | None:
    """Devuelve el tamaño o None si la operación no es viable (stop inválido o posición por debajo del mínimo)."""
    if precio_entrada <= 0 or distancia_stop <= 0 or riesgo_usd <= 0 or nocional_max <= 0:
        return None
    # pérdida por unidad si salta el stop: distancia + comisión de entrada y salida + deslizamiento de salida
    costo_unitario = distancia_stop + precio_entrada * (2 * comision_pct + slippage_pct) / 100
    cantidad = riesgo_usd / costo_unitario
    limitado_por = "riesgo"
    if cantidad * precio_entrada > nocional_max:
        cantidad = nocional_max / precio_entrada
        limitado_por = "nocional_maximo"
    nocional = cantidad * precio_entrada
    if nocional < nocional_min:
        return None
    return Tamano(cantidad=cantidad, nocional=nocional, riesgo_usd=cantidad * costo_unitario, limitado_por=limitado_por)
