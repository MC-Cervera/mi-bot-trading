"""Cliente de Claude compartido por noticias y decisiones.

- Respuestas en JSON con esquema fijo, validadas con pydantic (client.messages.parse).
- Control de presupuesto mensual ANTES de cada llamada: si se agotó, no llama y lo registra.
- Registra cada llamada (contexto, respuesta, tokens, costo) en la tabla llamadas_claude.
- Cualquier fallo (red, rechazo, JSON inválido) devuelve None: el llamador debe tratarlo como "no operar".
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypeVar

import anthropic
import pydantic
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bot.config import ConfigClaude
from bot.db.modelos import LlamadaClaude

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

# Margen reservado antes de cada llamada para no pasarse del presupuesto con la llamada en curso.
RESERVA_POR_LLAMADA_USD = 0.10


def ahora_ms() -> int:
    return int(time.time() * 1000)


def inicio_mes_ms(ts_ms: int) -> int:
    d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp() * 1000)


def calcular_costo(uso, precios) -> float:
    """Costo en USD a partir del objeto usage de la respuesta. Los tokens de razonamiento van en output_tokens."""
    def g(nombre):
        return getattr(uso, nombre, 0) or 0
    return (
        g("input_tokens") * precios.entrada
        + g("output_tokens") * precios.salida
        + g("cache_read_input_tokens") * precios.cache_lectura
        + g("cache_creation_input_tokens") * precios.cache_escritura
    ) / 1_000_000


@dataclass
class Resultado:
    datos: BaseModel | None
    llamada: LlamadaClaude

    @property
    def ok(self) -> bool:
        return self.datos is not None


class ClienteClaude:
    def __init__(self, config: ConfigClaude, sesion: Session, cliente: anthropic.Anthropic | None = None,
                 api_key: str | None = None):
        self.config = config
        self.sesion = sesion
        # max_retries: el SDK reintenta solo errores de red, 429 y 5xx
        self.cliente = cliente or anthropic.Anthropic(api_key=api_key or None, max_retries=3, timeout=120.0)

    # ---------- presupuesto ----------
    def gasto_mes_usd(self, ts_ms: int | None = None) -> float:
        desde = inicio_mes_ms(ts_ms or ahora_ms())
        total = self.sesion.scalar(select(func.sum(LlamadaClaude.costo_usd)).where(LlamadaClaude.ts_ms >= desde))
        return float(total or 0.0)

    def presupuesto_restante_usd(self) -> float:
        return self.config.presupuesto_mensual_usd - self.gasto_mes_usd()

    # ---------- llamada ----------
    def consultar(
        self, *, proposito: str, sistema: str, contenido: str, esquema: type[T], esfuerzo: str,
        max_tokens: int = 8000, disparador: str | None = None, par: str | None = None, senal_id: int | None = None,
    ) -> Resultado:
        llamada = LlamadaClaude(
            ts_ms=ahora_ms(), proposito=proposito, disparador=disparador, par=par, senal_id=senal_id,
            modelo=self.config.modelo, estado="", contexto=contenido,
        )
        datos = None
        if self.presupuesto_restante_usd() < RESERVA_POR_LLAMADA_USD:
            llamada.estado = "presupuesto_agotado"
            llamada.respuesta = (f"Presupuesto mensual de {self.config.presupuesto_mensual_usd} USD agotado "
                                 f"(gastado {self.gasto_mes_usd():.2f} USD). No se llamó a Claude.")
            log.warning(llamada.respuesta)
        else:
            try:
                r = self.cliente.messages.parse(
                    model=self.config.modelo,
                    max_tokens=max_tokens,
                    # el prompt de sistema es fijo: se cachea y las llamadas siguientes lo pagan ~10x más barato
                    system=[{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": contenido}],
                    thinking={"type": "adaptive"},
                    output_config={"effort": esfuerzo},
                    output_format=esquema,
                )
                self._anotar_uso(llamada, r)
                if r.stop_reason == "refusal":
                    llamada.estado = "rechazo"
                    llamada.respuesta = json.dumps({"stop_details": str(getattr(r, "stop_details", None))})
                elif r.stop_reason == "max_tokens":
                    llamada.estado = "invalida"
                    llamada.respuesta = "Respuesta cortada por max_tokens"
                elif r.parsed_output is None:
                    llamada.estado = "invalida"
                    llamada.respuesta = "Sin JSON en la respuesta"
                else:
                    datos = r.parsed_output
                    llamada.estado = "ok"
                    llamada.respuesta = datos.model_dump_json()
            except pydantic.ValidationError as e:
                llamada.estado = "invalida"
                llamada.respuesta = f"El JSON no cumple el esquema: {e}"
            except anthropic.APIConnectionError as e:
                llamada.estado = "error"
                llamada.respuesta = f"Error de conexión: {e}"
            except anthropic.RateLimitError as e:
                llamada.estado = "error"
                llamada.respuesta = f"Límite de peticiones: {e.message}"
            except anthropic.APIStatusError as e:
                llamada.estado = "error"
                llamada.respuesta = f"Error de la API ({e.status_code}): {e.message}"
            except Exception as e:  # noqa: BLE001 - ante cualquier fallo inesperado, el bot no opera
                log.exception("Fallo inesperado llamando a Claude")
                llamada.estado = "error"
                llamada.respuesta = f"Fallo inesperado: {type(e).__name__}: {e}"
            if llamada.estado != "ok":
                log.error("Llamada a Claude (%s) sin resultado válido: %s", proposito, llamada.estado)
        self.sesion.add(llamada)
        self.sesion.commit()
        return Resultado(datos=datos, llamada=llamada)

    def _anotar_uso(self, llamada: LlamadaClaude, r) -> None:
        u = r.usage
        llamada.tokens_entrada = u.input_tokens or 0
        llamada.tokens_salida = u.output_tokens or 0
        llamada.tokens_cache_lectura = getattr(u, "cache_read_input_tokens", 0) or 0
        llamada.tokens_cache_escritura = getattr(u, "cache_creation_input_tokens", 0) or 0
        llamada.costo_usd = calcular_costo(u, self.config.precios_usd_por_millon)
        llamada.id_solicitud = getattr(r, "_request_id", None)
