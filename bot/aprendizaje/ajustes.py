"""Ajustes de parámetros hechos por el aprendizaje.

Reglas no negociables:
- solo parámetros de `estrategia` (nunca las reglas de riesgo), y siempre dentro de [min, max] del dueño;
- cada ajuste guarda valor anterior, valor nuevo y justificación, y se puede revertir;
- la configuración efectiva = config.yaml + ajustes no revertidos (el más reciente por parámetro).
"""
from __future__ import annotations

import logging

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.config import Config
from bot.db.modelos import AjusteParametro, HistorialAprendizaje

log = logging.getLogger(__name__)


class AjusteInvalido(Exception):
    pass


def ajustes_vigentes(sesion: Session) -> list[AjusteParametro]:
    todos = sesion.scalars(select(AjusteParametro).where(AjusteParametro.revertido_ms.is_(None))
                           .order_by(AjusteParametro.creado_ms, AjusteParametro.id)).all()
    ultimo = {}
    for a in todos:
        ultimo[a.parametro] = a
    return list(ultimo.values())


def _con_valor(config: Config, parametro: str, valor: float) -> Config:
    datos = config.model_dump()
    if parametro not in datos["estrategia"]:
        raise AjusteInvalido(f"'{parametro}' no es un parámetro ajustable de la estrategia")
    datos["estrategia"][parametro]["valor"] = valor
    try:
        return Config.model_validate(datos)
    except ValidationError as e:
        raise AjusteInvalido(f"{parametro}={valor} no es válido: {e.errors()[0]['msg']}") from e


def config_efectiva(base: Config, sesion: Session) -> Config:
    config = base
    for a in ajustes_vigentes(sesion):
        try:
            config = _con_valor(config, a.parametro, a.valor_nuevo)
        except AjusteInvalido as e:  # p. ej. el dueño estrechó el rango después: el ajuste se ignora
            log.warning("Ajuste #%d ignorado: %s", a.id, e)
    return config


def validar_ajuste(config: Config, parametro: str, valor: float) -> None:
    _con_valor(config, parametro, valor)


def aplicar_ajuste(sesion: Session, base: Config, parametro: str, valor: float, justificacion: str, ahora_ms: int,
                   leccion_id: int | None = None) -> AjusteParametro:
    actual = config_efectiva(base, sesion)
    validar_ajuste(actual, parametro, valor)  # lanza AjusteInvalido si se sale del rango
    anterior = getattr(actual.estrategia, parametro).valor
    a = AjusteParametro(parametro=parametro, valor_anterior=anterior, valor_nuevo=valor, justificacion=justificacion,
                        leccion_id=leccion_id, creado_ms=ahora_ms)
    sesion.add(a)
    sesion.flush()
    sesion.add(HistorialAprendizaje(ts_ms=ahora_ms, entidad="ajuste", entidad_id=a.id, evento="aplicado",
                                    detalle=f"{parametro}: {anterior} → {valor}. {justificacion}"))
    sesion.commit()
    return a


def revertir_ajuste(sesion: Session, ajuste_id: int, motivo: str, ahora_ms: int) -> AjusteParametro:
    a = sesion.get(AjusteParametro, ajuste_id)
    if a is None or a.revertido_ms is not None:
        raise AjusteInvalido(f"El ajuste #{ajuste_id} no existe o ya fue revertido")
    a.revertido_ms = ahora_ms
    a.motivo_reversion = motivo
    sesion.add(HistorialAprendizaje(ts_ms=ahora_ms, entidad="ajuste", entidad_id=a.id, evento="revertido",
                                    detalle=f"{a.parametro} vuelve de {a.valor_nuevo} a su valor anterior. {motivo}"))
    sesion.commit()
    return a
