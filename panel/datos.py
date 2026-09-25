"""Consultas del panel. Funciones puras sobre la base de datos: la interfaz (app.py) solo las muestra."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bot.aprendizaje.ajustes import ajustes_vigentes
from bot.aprendizaje.condiciones import desde_json, mascara
from bot.aprendizaje.muestras import muestras_paper
from bot.backtest.metricas import calcular_metricas, max_drawdown_pct
from bot.cartera import CARTERA_CLAUDE, CARTERA_SOLO
from bot.db.modelos import (
    AjusteParametro, EstadoCartera, EventoRiesgo, HistorialAprendizaje, Hipotesis, Leccion, LlamadaClaude, Noticia,
    Operacion, PuntoCapital, Senal, Vela,
)

CARTERAS = (CARTERA_CLAUDE, CARTERA_SOLO)
NOMBRE_CARTERA = {CARTERA_CLAUDE: "Técnico + Claude", CARTERA_SOLO: "Técnico solo (control)"}
MOTIVOS = {"stop_loss": "Stop loss", "take_profit": "Objetivo", "cierre_diario": "Cierre 23:00",
           "claude_noticia": "Claude por noticia", "emergencia": "Emergencia", "drawdown_maximo": "Caída máxima"}


def fecha(ms: int | None) -> pd.Timestamp | None:
    return None if ms is None or pd.isna(ms) else pd.Timestamp(int(ms), unit="ms", tz="UTC")


def ultimos_precios(s: Session, exchange: str = "binance") -> dict[str, float]:
    """Cierre de la última vela guardada de cada par (el panel no llama al exchange)."""
    sub = select(Vela.par, func.max(Vela.ts).label("ts")).where(Vela.exchange == exchange).group_by(Vela.par).subquery()
    filas = s.execute(select(Vela.par, Vela.close).join(sub, (Vela.par == sub.c.par) & (Vela.ts == sub.c.ts))).all()
    return {p: float(c) for p, c in filas}


# ------------------------------------------------------------------ operaciones
def operaciones(s: Session, cartera: str | None = None) -> pd.DataFrame:
    q = select(Operacion).order_by(Operacion.ts_entrada_ms.desc())
    if cartera:
        q = q.where(Operacion.cartera == cartera)
    filas = [{
        "id": o.id, "cartera": o.cartera, "par": o.par, "direccion": o.direccion, "estado": o.estado,
        "entrada_fecha": fecha(o.ts_entrada_ms), "salida_fecha": fecha(o.ts_salida_ms),
        "precio_entrada": o.precio_entrada, "precio_salida": o.precio_salida, "resultado_usd": o.pnl_neto,
        "r_multiple": o.r_multiple, "comisiones_usd": o.comisiones, "funding_usd": o.funding,
        "motivo_salida": MOTIVOS.get(o.motivo_salida, o.motivo_salida), "confianza_claude": o.confianza_claude,
        "riesgo_usd": o.riesgo_usd, "nocional": o.nocional, "cantidad": o.cantidad,
    } for o in s.scalars(q)]
    return pd.DataFrame(filas)


def resultados_por_periodo(ops: pd.DataFrame, periodo: str) -> pd.DataFrame:
    """periodo: D (día), W (semana), M (mes), Y (año). Solo operaciones cerradas, por fecha de salida."""
    cerradas = ops[ops["estado"] == "cerrada"] if not ops.empty else ops
    if cerradas.empty:
        return pd.DataFrame(columns=["periodo", "cartera", "operaciones", "resultado_usd", "acierto_pct"])
    df = cerradas.assign(periodo=cerradas["salida_fecha"].dt.tz_convert(None).dt.to_period(
        {"D": "D", "W": "W", "M": "M", "Y": "Y"}[periodo]).dt.start_time)
    g = df.groupby(["periodo", "cartera"])
    out = pd.DataFrame({
        "operaciones": g.size(), "resultado_usd": g["resultado_usd"].sum(),
        "acierto_pct": g["resultado_usd"].apply(lambda x: (x > 0).mean() * 100),
    }).reset_index()
    return out.sort_values("periodo", ascending=False)


def curva_capital(s: Session) -> pd.DataFrame:
    filas = s.execute(select(PuntoCapital.cartera, PuntoCapital.ts_ms, PuntoCapital.capital)
                      .order_by(PuntoCapital.ts_ms)).all()
    df = pd.DataFrame(filas, columns=["cartera", "ts_ms", "capital"])
    if df.empty:
        return df.assign(fecha=pd.Series(dtype="datetime64[ns, UTC]"), drawdown_pct=pd.Series(dtype=float))
    df["fecha"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    df["drawdown_pct"] = df.groupby("cartera")["capital"].transform(lambda c: -(c.cummax() - c) / c.cummax() * 100)
    return df


def metricas(s: Session, cartera: str) -> dict:
    ops = operaciones(s, cartera)
    cerradas = ops[ops["estado"] == "cerrada"] if not ops.empty else ops
    est = s.get(EstadoCartera, cartera)
    capital_inicial = est.capital_inicial if est else 1000.0
    curva = curva_capital(s)
    curva = curva[curva["cartera"] == cartera].set_index("fecha")["capital"] if not curva.empty else pd.Series(dtype=float)
    if cerradas.empty:
        tabla = pd.DataFrame(columns=["pnl_neto", "r_multiple", "comisiones", "funding"])
    else:
        tabla = cerradas.rename(columns={"resultado_usd": "pnl_neto", "comisiones_usd": "comisiones",
                                         "funding_usd": "funding"})
    if curva.empty:
        curva = pd.Series([capital_inicial + tabla["pnl_neto"].sum() if len(tabla) else capital_inicial],
                          index=[pd.Timestamp.now(tz="UTC")])
    m = calcular_metricas(tabla, curva, capital_inicial)
    m["max_drawdown_pct"] = max_drawdown_pct(curva)
    return m


# ------------------------------------------------------------------ resumen
def resumen_carteras(s: Session, precios: dict[str, float] | None = None) -> list[dict]:
    precios = precios if precios is not None else ultimos_precios(s)
    hoy = int(pd.Timestamp.now(tz="UTC").normalize().timestamp() * 1000)
    salida = []
    for c in CARTERAS:
        est = s.get(EstadoCartera, c)
        inicial = est.capital_inicial if est else 1000.0
        cerradas = s.scalars(select(Operacion).where(Operacion.cartera == c, Operacion.estado == "cerrada")).all()
        abiertas = s.scalars(select(Operacion).where(Operacion.cartera == c, Operacion.estado == "abierta")).all()
        realizado = sum(o.pnl_neto or 0 for o in cerradas)
        hoy_realizado = sum(o.pnl_neto or 0 for o in cerradas if (o.ts_salida_ms or 0) >= hoy)
        no_realizado = 0.0
        valor_abiertas = 0.0
        for o in abiertas:
            p = precios.get(o.par, o.precio_entrada)
            d = 1 if o.direccion == "largo" else -1
            no_realizado += (p - o.precio_entrada) * o.cantidad * d - o.funding
            valor_abiertas += p * o.cantidad
        salida.append({
            "cartera": c, "nombre": NOMBRE_CARTERA[c], "estado": est.estado if est else "sin iniciar",
            "motivo": est.motivo if est else "", "capital_inicial": inicial,
            "capital": inicial + realizado + no_realizado, "valor_posiciones": valor_abiertas,
            "pnl_total": realizado + no_realizado, "pnl_hoy": hoy_realizado + no_realizado,
            "posiciones_abiertas": len(abiertas), "operaciones_cerradas": len(cerradas),
        })
    return salida


def posiciones_abiertas(s: Session, precios: dict[str, float] | None = None,
                        ahora: pd.Timestamp | None = None) -> pd.DataFrame:
    """Posiciones abiertas ahora, valoradas con el último precio guardado. Resultado sin la comisión de salida."""
    precios = precios if precios is not None else ultimos_precios(s)
    ahora = ahora if ahora is not None else pd.Timestamp.now(tz="UTC")
    filas = []
    for o in s.scalars(select(Operacion).where(Operacion.estado == "abierta").order_by(Operacion.ts_entrada_ms)):
        d = 1 if o.direccion == "largo" else -1
        p = precios.get(o.par, o.precio_entrada)
        resultado = (p - o.precio_entrada) * o.cantidad * d - (o.funding or 0)
        entrada = fecha(o.ts_entrada_ms)
        filas.append({
            "id": o.id, "cartera": NOMBRE_CARTERA.get(o.cartera, o.cartera), "par": o.par, "direccion": o.direccion,
            "entrada_fecha": entrada, "horas_abierta": (ahora - entrada).total_seconds() / 3600,
            "precio_entrada": o.precio_entrada, "precio_actual": p, "stop_loss": o.stop_loss,
            "take_profit": o.take_profit, "resultado_usd": resultado,
            "resultado_r": resultado / o.riesgo_usd if o.riesgo_usd else None,
            "dist_sl_pct": abs(p - o.stop_loss) / p * 100, "dist_tp_pct": abs(o.take_profit - p) / p * 100,
            "nocional": o.nocional,
        })
    return pd.DataFrame(filas)


def eventos(s: Session, limite: int = 50, tipo: str | None = None) -> pd.DataFrame:
    q = select(EventoRiesgo).order_by(EventoRiesgo.id.desc()).limit(limite)
    if tipo:
        q = q.where(EventoRiesgo.tipo == tipo)
    return pd.DataFrame([{"fecha": fecha(e.ts_ms), "cartera": e.cartera, "tipo": e.tipo, "par": e.par,
                          "detalle": e.detalle} for e in s.scalars(q)])


# ------------------------------------------------------------------ detalle
def detalle_operacion(s: Session, op_id: int) -> dict | None:
    op = s.get(Operacion, op_id)
    if op is None:
        return None
    senal = s.get(Senal, op.senal_id) if op.senal_id else None
    llamada = s.get(LlamadaClaude, op.llamada_claude_id) if op.llamada_claude_id else None
    decision = json.loads(llamada.respuesta) if llamada and llamada.estado == "ok" and llamada.respuesta else None
    codigos = json.loads(op.lecciones_aplicadas or "[]")
    lecciones = [{"codigo": l.codigo, "enunciado": l.enunciado, "estado": l.estado}
                 for l in s.scalars(select(Leccion).where(Leccion.codigo.in_(codigos)))] if codigos else []
    return {
        "operacion": op, "senal": senal, "llamada": llamada, "decision": decision, "lecciones": lecciones,
        "reglas": json.loads(op.reglas_que_permitieron or "[]"),
    }


# ------------------------------------------------------------------ aprendizaje
def hipotesis(s: Session, texto: str = "", estados: list[str] | None = None, origenes: list[str] | None = None) -> list[Hipotesis]:
    q = select(Hipotesis).order_by(Hipotesis.id.desc())
    if estados:
        q = q.where(Hipotesis.estado.in_(estados))
    if origenes:
        q = q.where(Hipotesis.origen.in_(origenes))
    res = list(s.scalars(q))
    t = texto.strip().lower()
    return [h for h in res if not t or t in f"{h.codigo} {h.enunciado} {h.explicacion}".lower()]


def lecciones(s: Session, texto: str = "", estados: list[str] | None = None) -> list[Leccion]:
    q = select(Leccion).order_by(Leccion.id.desc())
    if estados:
        q = q.where(Leccion.estado.in_(estados))
    t = texto.strip().lower()
    return [l for l in s.scalars(q) if not t or t in f"{l.codigo} {l.enunciado} {l.explicacion}".lower()]


def historial(s: Session, entidad: str, entidad_id: int) -> pd.DataFrame:
    q = select(HistorialAprendizaje).where(HistorialAprendizaje.entidad == entidad,
                                           HistorialAprendizaje.entidad_id == entidad_id).order_by(HistorialAprendizaje.id)
    return pd.DataFrame([{"fecha": fecha(e.ts_ms), "evento": e.evento, "detalle": e.detalle} for e in s.scalars(q)])


def operaciones_relacionadas(s: Session, h: Hipotesis) -> pd.DataFrame:
    """Operaciones del paper trading que cumplen las condiciones de una hipótesis de filtro."""
    if h.tipo != "filtro":
        return pd.DataFrame()
    base = muestras_paper(s)
    if base.empty:
        return base
    return base[mascara(desde_json(h.condiciones), base)].sort_values("ts_entrada", ascending=False)


def ajustes(s: Session) -> pd.DataFrame:
    vigentes = {a.id for a in ajustes_vigentes(s)}
    return pd.DataFrame([{
        "id": a.id, "parametro": a.parametro, "antes": a.valor_anterior, "despues": a.valor_nuevo,
        "fecha": fecha(a.creado_ms), "estado": "vigente" if a.id in vigentes else ("revertido" if a.revertido_ms else "sustituido"),
        "justificacion": a.justificacion, "motivo_reversion": a.motivo_reversion,
    } for a in s.scalars(select(AjusteParametro).order_by(AjusteParametro.id.desc()))])


def evidencia_legible(evidencia_json: str | None) -> str | None:
    if not evidencia_json:
        return None
    return json.loads(evidencia_json).get("explicacion")


# ------------------------------------------------------------------ noticias
def noticias(s: Session, horas: int = 72, solo_relevantes: bool = True) -> pd.DataFrame:
    desde = int((pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=horas)).timestamp() * 1000)
    q = select(Noticia).where(Noticia.publicada_ms >= desde).order_by(Noticia.publicada_ms.desc())
    if solo_relevantes:
        q = q.where(Noticia.relevante.is_(True))
    return pd.DataFrame([{
        "fecha": fecha(n.publicada_ms), "fuente": n.fuente, "titulo": n.titulo, "resumen": n.resumen,
        "activos": ", ".join(json.loads(n.activos or "[]")), "sentimiento": n.sentimiento, "impacto": n.impacto,
        "horizonte": n.horizonte, "tema": n.tema, "url": n.url,
    } for n in s.scalars(q)])


# ------------------------------------------------------------------ costos
def costos_claude(s: Session) -> pd.DataFrame:
    filas = s.execute(select(LlamadaClaude.ts_ms, LlamadaClaude.proposito, LlamadaClaude.costo_usd,
                             LlamadaClaude.estado)).all()
    df = pd.DataFrame(filas, columns=["ts_ms", "proposito", "costo_usd", "estado"])
    df["fecha"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    return df


def valor_de_claude(s: Session) -> dict:
    """¿Se paga sola la IA? (resultado técnico+Claude − resultado técnico solo) − gasto en Claude."""
    res = {c: sum(o.pnl_neto or 0 for o in s.scalars(select(Operacion).where(
        Operacion.cartera == c, Operacion.estado == "cerrada"))) for c in CARTERAS}
    gasto = float(s.scalar(select(func.sum(LlamadaClaude.costo_usd))) or 0.0)
    aporte = res[CARTERA_CLAUDE] - res[CARTERA_SOLO]
    return {"resultado_claude": res[CARTERA_CLAUDE], "resultado_solo": res[CARTERA_SOLO], "aporte_bruto": aporte,
            "gasto_claude": gasto, "aporte_neto": aporte - gasto}


def serie_valor_claude(s: Session) -> pd.DataFrame:
    """Por día: gasto acumulado en Claude y diferencia acumulada de resultados (Claude − control)."""
    ops = operaciones(s)
    gastos = costos_claude(s)
    partes = []
    if not ops.empty:
        c = ops[ops["estado"] == "cerrada"].copy()
        if not c.empty:
            c["dia"] = c["salida_fecha"].dt.floor("D")
            c["signo"] = np.where(c["cartera"] == CARTERA_CLAUDE, 1.0, -1.0)
            partes.append((c["resultado_usd"] * c["signo"]).groupby(c["dia"]).sum().rename("diferencia"))
    if not gastos.empty:
        gastos["dia"] = gastos["fecha"].dt.floor("D")
        partes.append(gastos.groupby("dia")["costo_usd"].sum().rename("gasto"))
    if not partes:
        return pd.DataFrame(columns=["dia", "diferencia_acumulada", "gasto_acumulado", "neto_acumulado"])
    df = pd.concat(partes, axis=1).fillna(0.0).sort_index()
    for col in ("diferencia", "gasto"):
        if col not in df:
            df[col] = 0.0
    df["diferencia_acumulada"] = df["diferencia"].cumsum()
    df["gasto_acumulado"] = df["gasto"].cumsum()
    df["neto_acumulado"] = df["diferencia_acumulada"] - df["gasto_acumulado"]
    return df.reset_index(names="dia")
