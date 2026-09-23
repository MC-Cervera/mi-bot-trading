"""Impacto REAL de las noticias en el precio y estadísticas por tema.

Para cada noticia analizada y cada activo afectado se mide cuánto se movió el precio 1 h, 4 h y 24 h después de
publicarse, y cuánto de ese movimiento fue "propio" (anormal = movimiento del activo menos el promedio del mercado).
Con eso se construye, por tema e impacto, un historial que se le pasa a Claude ante noticias parecidas y que tú
verás en el panel ("¿las noticias de regulación de impacto alto realmente mueven el precio?").
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.db.modelos import ImpactoNoticia, Noticia, Vela

H = 3_600_000
HORIZONTES = {"ret_1h": 1, "ret_4h": 4, "ret_24h": 24}
UMBRAL_SENTIMIENTO = 0.2  # por debajo de esto la noticia se considera neutral para medir aciertos de dirección


def _precios(sesion: Session, exchange: str, par: str, desde: int, hasta: int) -> pd.DataFrame:
    filas = sesion.execute(
        select(Vela.ts, Vela.open, Vela.close).where(
            Vela.exchange == exchange, Vela.par == par, Vela.temporalidad == "1h", Vela.ts >= desde, Vela.ts <= hasta,
        ).order_by(Vela.ts)
    ).all()
    return pd.DataFrame(filas, columns=["ts", "open", "close"]).set_index("ts")


def _retornos(df: pd.DataFrame, t0: int) -> tuple[float | None, dict]:
    """Precio de referencia = apertura de la primera vela que empieza DESPUÉS de publicarse la noticia."""
    inicio = df[df.index >= t0]
    if inicio.empty:
        return None, {}
    ts0 = int(inicio.index[0])
    p0 = float(inicio["open"].iloc[0])
    out = {}
    for col, h in HORIZONTES.items():
        ts_fin = ts0 + (h - 1) * H  # cierre de la vela h-ésima
        out[col] = (float(df.at[ts_fin, "close"]) / p0 - 1) * 100 if ts_fin in df.index else None
    return p0, out


def medir_impactos(sesion: Session, exchange: str, pares: list[str], ahora_ms: int) -> int:
    """Calcula o completa el impacto de las noticias relevantes con al menos 1 h de antigüedad. Devuelve cuántos actualizó."""
    bases = {p.split("/")[0]: p for p in pares}
    noticias = sesion.scalars(select(Noticia).where(
        Noticia.relevante.is_(True), Noticia.publicada_ms <= ahora_ms - H,
        Noticia.publicada_ms >= ahora_ms - 30 * 24 * H,
    )).all()
    actualizados = 0
    for n in noticias:
        activos = json.loads(n.activos or "[]")
        objetivo = [bases[a] for a in activos if a in bases]
        if "MERCADO" in activos and not objetivo:
            objetivo = [bases["BTC"]] if "BTC" in bases else []
        if not objetivo:
            continue
        existentes = {i.par: i for i in sesion.scalars(select(ImpactoNoticia).where(ImpactoNoticia.noticia_id == n.id))}
        pendientes = [p for p in objetivo if p not in existentes or not existentes[p].completo]
        if not pendientes:
            continue
        # retorno medio del mercado (todos los pares) en la misma ventana, para aislar el efecto propio de la noticia
        mercado = {}
        for p in pares:
            _, r = _retornos(_precios(sesion, exchange, p, n.publicada_ms - H, n.publicada_ms + 26 * H), n.publicada_ms)
            mercado[p] = r
        for par in pendientes:
            p0, r = _retornos(_precios(sesion, exchange, par, n.publicada_ms - H, n.publicada_ms + 26 * H), n.publicada_ms)
            if p0 is None:
                continue
            imp = existentes.get(par) or ImpactoNoticia(noticia_id=n.id, par=par, precio_inicial=p0)
            imp.precio_inicial = p0
            for col in HORIZONTES:
                setattr(imp, col, r.get(col))
            for col, anormal in (("ret_4h", "anormal_4h"), ("ret_24h", "anormal_24h")):
                vals = [m[col] for m in mercado.values() if m.get(col) is not None]
                media = sum(vals) / len(vals) if vals else None
                setattr(imp, anormal, None if r.get(col) is None or media is None else r[col] - media)
            imp.completo = r.get("ret_24h") is not None
            sesion.add(imp)
            actualizados += 1
    sesion.commit()
    return actualizados


def tabla_impactos(sesion: Session) -> pd.DataFrame:
    filas = sesion.execute(
        select(Noticia.id, Noticia.titulo, Noticia.tema, Noticia.impacto, Noticia.sentimiento, Noticia.publicada_ms,
               ImpactoNoticia.par, ImpactoNoticia.ret_1h, ImpactoNoticia.ret_4h, ImpactoNoticia.ret_24h,
               ImpactoNoticia.anormal_4h, ImpactoNoticia.anormal_24h)
        .join(ImpactoNoticia, ImpactoNoticia.noticia_id == Noticia.id)
    ).all()
    return pd.DataFrame(filas, columns=["noticia_id", "titulo", "tema", "impacto", "sentimiento", "publicada_ms", "par",
                                        "ret_1h", "ret_4h", "ret_24h", "anormal_4h", "anormal_24h"])


def estadisticas_impacto(sesion: Session) -> pd.DataFrame:
    """Por tema e impacto: cuántas noticias, cuánto se movió el precio y si se movió hacia donde decía el sentimiento."""
    df = tabla_impactos(sesion)
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    direccion = df["sentimiento"].apply(lambda s: 1 if s >= UMBRAL_SENTIMIENTO else (-1 if s <= -UMBRAL_SENTIMIENTO else 0))
    df["mov_a_favor_4h"] = df["ret_4h"] * direccion  # positivo = el precio fue en la dirección del sentimiento
    df["acierto_4h"] = (df["ret_4h"] * direccion > 0).where((direccion != 0) & df["ret_4h"].notna())
    g = df.groupby(["tema", "impacto"])
    return pd.DataFrame({
        "muestras": g.size(),
        "mov_abs_medio_1h_pct": g["ret_1h"].apply(lambda s: s.abs().mean()),
        "mov_abs_medio_4h_pct": g["ret_4h"].apply(lambda s: s.abs().mean()),
        "mov_abs_medio_24h_pct": g["ret_24h"].apply(lambda s: s.abs().mean()),
        "mov_medio_a_favor_4h_pct": g["mov_a_favor_4h"].mean(),
        "anormal_abs_medio_4h_pct": g["anormal_4h"].apply(lambda s: s.abs().mean()),
        "acierto_direccion_4h_pct": g["acierto_4h"].apply(lambda s: s.dropna().mean() * 100 if s.notna().any() else None),
    }).reset_index()


@dataclass
class HistorialTema:
    tema: str
    impacto: str
    muestras: int
    texto: str


def historial_para(stats: pd.DataFrame, tema: str, impacto: str, minimo: int) -> HistorialTema:
    """Resumen en una línea del impacto histórico de noticias parecidas, para el contexto de Claude."""
    fila = stats[(stats["tema"] == tema) & (stats["impacto"] == impacto)] if not stats.empty else stats
    n = int(fila["muestras"].iloc[0]) if len(fila) else 0
    if n < minimo:
        return HistorialTema(tema, impacto, n, f"Sin historial suficiente ({n} noticias parecidas, mínimo {minimo}).")
    f = fila.iloc[0]
    acierto = f["acierto_direccion_4h_pct"]
    return HistorialTema(tema, impacto, n, (
        f"{n} noticias previas de tema '{tema}' e impacto {impacto}: movimiento medio a 4 h {f['mov_abs_medio_4h_pct']:.2f}% "
        f"(a 24 h {f['mov_abs_medio_24h_pct']:.2f}%), movimiento propio del activo {f['anormal_abs_medio_4h_pct']:.2f}%; "
        f"el precio fue en la dirección del sentimiento el "
        + (f"{acierto:.0f}% de las veces." if acierto is not None and not pd.isna(acierto) else "— (sin dirección clara).")
    ))
