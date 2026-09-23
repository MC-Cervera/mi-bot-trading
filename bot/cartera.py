"""Gestor de una cartera de paper trading: estado, aperturas, cierres, stops, límites y curva de capital.

Hay dos carteras con los mismos precios y señales: "tecnico_claude" y "tecnico_solo" (grupo de control).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bot.config import Config
from bot.db.modelos import EstadoCartera, EventoRiesgo, Operacion, PuntoCapital
from bot.diario import analisis_posterior, texto_salida
from bot.ejecucion.base import Broker, ErrorEjecucion
from bot.notificaciones import Notificador
from bot.riesgo import ACTIVO, DETENIDO, PAUSADO, EstadoRiesgo, Propuesta, Veredicto, transicion_por_limites

log = logging.getLogger(__name__)

CARTERA_CLAUDE = "tecnico_claude"
CARTERA_SOLO = "tecnico_solo"
HORAS_FUNDING = (0, 8, 16)


def ms(ts: pd.Timestamp) -> int:
    return int(ts.timestamp() * 1000)


def inicio_dia_ms(ts: pd.Timestamp) -> int:
    return ms(ts.normalize())


class GestorCartera:
    def __init__(self, sesion: Session, config: Config, nombre: str, broker: Broker, avisos: Notificador | None = None):
        self.s = sesion
        self.config = config
        self.nombre = nombre
        self.broker = broker
        self.avisos = avisos or Notificador()
        self.funding_pct = config.backtest.funding_pct_8h / 100

    # ------------------------------------------------------------------ estado
    def registro(self) -> EstadoCartera:
        est = self.s.get(EstadoCartera, self.nombre)
        if est is None:
            capital = self.config.capital_operativo
            est = EstadoCartera(cartera=self.nombre, capital_inicial=capital, pico=capital, estado=ACTIVO, motivo="")
            self.s.add(est)
            self.s.commit()
        return est

    def abiertas(self) -> list[Operacion]:
        return list(self.s.scalars(select(Operacion).where(Operacion.cartera == self.nombre, Operacion.estado == "abierta")))

    def realizado(self, hasta_ms: int | None = None) -> float:
        q = select(func.sum(Operacion.pnl_neto)).where(Operacion.cartera == self.nombre, Operacion.estado == "cerrada")
        if hasta_ms is not None:
            q = q.where(Operacion.ts_salida_ms < hasta_ms)
        return float(self.s.scalar(q) or 0.0)

    def no_realizado(self, precios: dict[str, float]) -> float:
        total = 0.0
        for op in self.abiertas():
            p = precios.get(op.par)
            if p is not None:
                d = 1 if op.direccion == "largo" else -1
                total += (p - op.precio_entrada) * op.cantidad * d - op.funding
        return total

    def estado_riesgo(self, precios: dict[str, float], ahora: pd.Timestamp) -> EstadoRiesgo:
        est = self.registro()
        # la pausa diaria termina al empezar un nuevo día UTC
        if est.estado == PAUSADO and est.pausado_dia != ahora.strftime("%Y-%m-%d"):
            self._cambiar_estado(est, ACTIVO, "Nuevo día: se levanta la pausa diaria", ahora)
        realizado = self.realizado()
        capital = est.capital_inicial + realizado + self.no_realizado(precios)
        if capital > est.pico:
            est.pico = capital
            self.s.commit()
        hoy = inicio_dia_ms(ahora)
        return EstadoRiesgo(
            estado=est.estado, motivo=est.motivo, capital=capital, pico=est.pico,
            capital_inicio_dia=est.capital_inicial + self.realizado(hasta_ms=hoy),
            pnl_dia=realizado - self.realizado(hasta_ms=hoy),
            posiciones={op.par: op.nocional for op in self.abiertas()},
        )

    def _cambiar_estado(self, est: EstadoCartera, nuevo: str, motivo: str, ahora: pd.Timestamp) -> None:
        est.estado = nuevo
        est.motivo = motivo
        est.pausado_dia = ahora.strftime("%Y-%m-%d") if nuevo == PAUSADO else None
        self.s.commit()
        self.evento(f"estado_{nuevo}", motivo, ahora=ahora)

    def evento(self, tipo: str, detalle: str, par: str | None = None, senal_id: int | None = None,
               llamada_id: int | None = None, ahora: pd.Timestamp | None = None) -> None:
        self.s.add(EventoRiesgo(ts_ms=ms(ahora) if ahora is not None else int(datetime.now(timezone.utc).timestamp() * 1000),
                                cartera=self.nombre, tipo=tipo, par=par, detalle=detalle, senal_id=senal_id,
                                llamada_claude_id=llamada_id))
        self.s.commit()

    # ------------------------------------------------------------------ apertura
    def abrir(self, p: Propuesta, v: Veredicto, *, ts_senal: pd.Timestamp, ahora: pd.Timestamp, diario: str,
              senal_id: int | None = None, llamada_id: int | None = None, lecciones: str = "[]") -> Operacion | None:
        if not v.permitido:
            self.evento("bloqueo", " | ".join(v.bloqueos), par=p.par, senal_id=senal_id, llamada_id=llamada_id, ahora=ahora)
            return None
        id_cliente = f"{self.nombre}-{p.par.replace('/', '')}-{ms(ts_senal)}"
        if self.s.scalar(select(Operacion.id).where(Operacion.id_cliente == id_cliente)):
            log.info("Señal %s ya operada en %s: no se duplica", id_cliente, self.nombre)
            return None
        t = v.tamano
        try:
            ej = self.broker.abrir(id_cliente, p.par, p.direccion, t.cantidad, p.precio_entrada, p.stop_loss, p.take_profit)
        except ErrorEjecucion as e:
            self.evento("error_ejecucion", str(e), par=p.par, senal_id=senal_id, llamada_id=llamada_id, ahora=ahora)
            self.avisos.enviar(f"⚠️ [{self.nombre}] Error abriendo {p.par}: {e}")
            return None
        # stop y objetivo se recalculan desde el precio real de entrada manteniendo las distancias planificadas
        d = 1 if p.direccion == "largo" else -1
        dist_sl, dist_tp = abs(p.precio_entrada - p.stop_loss), abs(p.take_profit - p.precio_entrada)
        op = Operacion(
            id_cliente=id_cliente, cartera=self.nombre, par=p.par, direccion=p.direccion, estado="abierta",
            ts_senal_ms=ms(ts_senal), ts_entrada_ms=ms(ahora), precio_entrada=ej.precio, cantidad=ej.cantidad,
            nocional=ej.precio * ej.cantidad, stop_loss=ej.precio - d * dist_sl, take_profit=ej.precio + d * dist_tp,
            riesgo_usd=t.riesgo_usd, comisiones=ej.comision, funding=0.0, ultimo_funding_ms=ms(ahora),
            max_favorable=ej.precio, max_adverso=ej.precio, senal_id=senal_id, llamada_claude_id=llamada_id,
            sugerida_por=p.origen, confianza_claude=p.confianza, lecciones_aplicadas=lecciones,
            reglas_que_permitieron=json.dumps(v.reglas_cumplidas, ensure_ascii=False),
            ordenes_exchange=json.dumps(ej.ordenes), diario_entrada=diario,
        )
        self.s.add(op)
        try:
            self.s.commit()
        except IntegrityError:
            self.s.rollback()
            log.warning("Operación duplicada evitada por la base de datos: %s", id_cliente)
            return None
        self.avisos.enviar(
            f"🟢 [{self.nombre}] {p.direccion.upper()} {p.par} a {op.precio_entrada:.6g}\n"
            f"SL {op.stop_loss:.6g} · TP {op.take_profit:.6g} · riesgo {op.riesgo_usd:.2f} USD"
            + (f" · confianza Claude {p.confianza:.2f}" if p.confianza is not None else ""))
        return op

    # ------------------------------------------------------------------ cierre
    def cerrar(self, op: Operacion, precio: float, motivo: str, ahora: pd.Timestamp, ejecutado: bool = False,
               comision_salida: float | None = None) -> Operacion:
        """Cierra la operación. `ejecutado=True` si el exchange ya la cerró (solo se registra)."""
        if ejecutado:
            precio_final, comision = precio, comision_salida or 0.0
        else:
            ej = self.broker.cerrar(op.id_cliente, op.par, op.direccion, op.cantidad, precio, motivo)
            precio_final, comision = ej.precio, ej.comision
        d = 1 if op.direccion == "largo" else -1
        op.precio_salida = precio_final
        op.ts_salida_ms = ms(ahora)
        op.comisiones += comision
        op.pnl_neto = (precio_final - op.precio_entrada) * op.cantidad * d - op.comisiones - op.funding
        op.r_multiple = op.pnl_neto / op.riesgo_usd if op.riesgo_usd else 0.0
        op.motivo_salida = motivo
        op.estado = "cerrada"
        op.diario_salida = texto_salida(op)
        op.analisis_post = analisis_posterior(op)
        self.s.commit()
        icono = "✅" if op.pnl_neto > 0 else "🔴"
        self.avisos.enviar(f"{icono} [{self.nombre}] Cerrado {op.par} ({op.motivo_salida}): {op.pnl_neto:+.2f} USD "
                           f"({op.r_multiple:+.2f}R)")
        return op

    def cerrar_todo(self, precios: dict[str, float], motivo: str, ahora: pd.Timestamp) -> list[Operacion]:
        cerradas = []
        for op in self.abiertas():
            p = precios.get(op.par)
            if p is None:
                self.evento("error_ejecucion", f"Sin precio para cerrar {op.par} ({motivo})", par=op.par, ahora=ahora)
                continue
            cerradas.append(self.cerrar(op, p, motivo, ahora))
        return cerradas

    # ------------------------------------------------------------------ vigilancia
    def vigilar(self, precios: dict[str, float], extremos: dict[str, tuple[float, float]], ahora: pd.Timestamp) -> list[Operacion]:
        """Revisa stops y objetivos. `extremos`: par -> (máximo, mínimo) de las velas desde la última revisión.

        Si en el mismo intervalo se tocaron stop y objetivo, se asume el stop (no se sabe qué fue primero).
        """
        cerradas = []
        if self.broker.gestiona_stops:
            for c in self.broker.cierres_en_exchange(self.abiertas()):
                op = next(o for o in self.abiertas() if o.par == c.par)
                cerradas.append(self.cerrar(op, c.precio, c.motivo, ahora, ejecutado=True, comision_salida=c.comision))
        for op in self.abiertas():
            p = precios.get(op.par)
            if p is None:
                continue
            alto, bajo = extremos.get(op.par, (p, p))
            alto, bajo = max(alto, p), min(bajo, p)
            d = 1 if op.direccion == "largo" else -1
            mejor, peor = op.max_favorable or op.precio_entrada, op.max_adverso or op.precio_entrada
            if d == 1:
                op.max_favorable, op.max_adverso = max(mejor, alto), min(peor, bajo)
            else:
                op.max_favorable, op.max_adverso = min(mejor, bajo), max(peor, alto)
            if self.broker.gestiona_stops:
                continue
            toco_stop = (bajo <= op.stop_loss) if d == 1 else (alto >= op.stop_loss)
            toco_tp = (alto >= op.take_profit) if d == 1 else (bajo <= op.take_profit)
            if toco_stop:
                # si el precio actual ya está más allá del stop (hueco), se ejecuta al actual, que es peor
                precio = min(p, op.stop_loss) if d == 1 else max(p, op.stop_loss)
                cerradas.append(self.cerrar(op, precio, "stop_loss", ahora))
            elif toco_tp:
                cerradas.append(self.cerrar(op, op.take_profit, "take_profit", ahora))
        self.s.commit()
        return cerradas

    def cobrar_funding(self, precios: dict[str, float], ahora: pd.Timestamp) -> None:
        """Funding de futuros cobrado siempre como costo (supuesto conservador) a las 00, 08 y 16 UTC."""
        for op in self.abiertas():
            for h in HORAS_FUNDING:
                t = ms(ahora.normalize() + pd.Timedelta(hours=h))
                if op.ultimo_funding_ms < t <= ms(ahora) and op.par in precios:
                    op.funding += op.cantidad * precios[op.par] * self.funding_pct
                    op.ultimo_funding_ms = t
        self.s.commit()

    def aplicar_limites(self, precios: dict[str, float], ahora: pd.Timestamp) -> str | None:
        """Pausa por pérdida diaria o parada por drawdown. En la parada se cierra todo y se avisa."""
        e = self.estado_riesgo(precios, ahora)
        t = transicion_por_limites(e, self.config)
        if t is None:
            return None
        nuevo, motivo = t
        if nuevo == DETENIDO:
            self.cerrar_todo(precios, "drawdown_maximo", ahora)
        self._cambiar_estado(self.registro(), nuevo, motivo, ahora)
        icono = "🛑" if nuevo == DETENIDO else "⏸️"
        self.avisos.enviar(f"{icono} [{self.nombre}] {motivo}")
        return motivo

    def emergencia(self, precios: dict[str, float], ahora: pd.Timestamp, motivo: str = "Botón de emergencia") -> None:
        self.cerrar_todo(precios, "emergencia", ahora)
        self._cambiar_estado(self.registro(), DETENIDO, motivo, ahora)
        self.avisos.enviar(f"🚨 [{self.nombre}] EMERGENCIA: posiciones cerradas y bot detenido. {motivo}")

    def reactivar(self, ahora: pd.Timestamp, quien: str) -> None:
        est = self.registro()
        est.pico = est.capital_inicial + self.realizado()  # el drawdown vuelve a medirse desde el capital actual
        self._cambiar_estado(est, ACTIVO, f"Reactivado manualmente por {quien}", ahora)

    def foto_capital(self, precios: dict[str, float], ahora: pd.Timestamp) -> None:
        est = self.registro()
        realizado = self.realizado()
        punto = self.s.scalar(select(PuntoCapital).where(PuntoCapital.cartera == self.nombre, PuntoCapital.ts_ms == ms(ahora)))
        punto = punto or PuntoCapital(cartera=self.nombre, ts_ms=ms(ahora))
        punto.capital = est.capital_inicial + realizado + self.no_realizado(precios)
        punto.realizado = realizado
        punto.posiciones_abiertas = len(self.abiertas())
        self.s.add(punto)
        self.s.commit()
