"""Arma el ContextoDecision a partir de la base de datos. Solo usa información disponible en el momento `ahora`."""
from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from bot.config import Config
from bot.ia.decision import ContextoDecision
from bot.noticias.analisis import noticias_recientes
from bot.noticias.impacto import estadisticas_impacto, historial_para
from bot.senales import SenalCandidata


def reglas_para_claude(config: Config) -> dict:
    r = config.riesgo
    return {
        "perdida_maxima_por_operacion_usd": r.sl_usd_por_operacion,
        "riesgo_maximo_pct_capital": r.riesgo_max_pct_operacion,
        "max_posiciones": r.max_posiciones,
        "perdida_diaria_maxima_pct": r.perdida_diaria_max_pct,
        "caida_maxima_pct": r.drawdown_max_pct,
        "confianza_minima": max(r.confianza_min_claude, config.estrategia.confianza_min.valor),
        "apalancamiento": r.apalancamiento,
        "cierre_diario_utc": r.hora_cierre_diario_utc,
    }


def historial_de(sesion: Session, noticias, minimo: int) -> list[str]:
    stats = estadisticas_impacto(sesion)
    vistos, textos = set(), []
    for n in noticias:
        clave = (n.tema, n.impacto)
        if n.tema and n.impacto and clave not in vistos:
            vistos.add(clave)
            textos.append(historial_para(stats, n.tema, n.impacto, minimo).texto)
    return textos


def contexto_para_senal(
    sesion: Session, config: Config, senal: SenalCandidata, df_senales: pd.DataFrame, ahora: pd.Timestamp,
    posiciones_abiertas: list[dict], capital_usd: float, libre_usd: float, lecciones: list[dict] | None = None,
    n_velas: int = 24,
) -> ContextoDecision:
    ahora_ms = int(ahora.timestamp() * 1000)
    base = senal.par.split("/")[0]
    noticias = noticias_recientes(sesion, base, ahora_ms - config.noticias.horas_contexto * 3_600_000, ahora_ms)
    velas = df_senales[df_senales.index <= senal.ts_vela].tail(n_velas)
    return ContextoDecision(
        disparador="senal", par=senal.par, ahora=ahora, precio_actual=senal.precio_referencia, senal=senal,
        velas_recientes=velas, noticias=noticias,
        historial_noticias=historial_de(sesion, noticias, config.noticias.min_muestras_estadistica),
        posiciones_abiertas=posiciones_abiertas, capital_usd=capital_usd, libre_usd=libre_usd,
        lecciones=lecciones or [], reglas=reglas_para_claude(config),
    )


def contexto_para_noticia(
    sesion: Session, config: Config, noticia, par: str, precio_actual: float, df_senales: pd.DataFrame | None,
    ahora: pd.Timestamp, posiciones_abiertas: list[dict], capital_usd: float, libre_usd: float,
    lecciones: list[dict] | None = None, n_velas: int = 24,
) -> ContextoDecision:
    ahora_ms = int(ahora.timestamp() * 1000)
    noticias = noticias_recientes(sesion, par.split("/")[0], ahora_ms - config.noticias.horas_contexto * 3_600_000, ahora_ms)
    return ContextoDecision(
        disparador="noticia_alto_impacto", par=par, ahora=ahora, precio_actual=precio_actual,
        velas_recientes=None if df_senales is None else df_senales[df_senales.index <= ahora].tail(n_velas),
        noticias=noticias, noticia_disparadora=noticia,
        historial_noticias=historial_de(sesion, [noticia, *noticias], config.noticias.min_muestras_estadistica),
        posiciones_abiertas=posiciones_abiertas, capital_usd=capital_usd, libre_usd=libre_usd,
        lecciones=lecciones or [], reglas=reglas_para_claude(config),
    )
