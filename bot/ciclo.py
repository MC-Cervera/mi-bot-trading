"""Ciclo del bot en paper trading.

- ciclo_horario (minuto 1 de cada hora, con la vela recién cerrada): actualiza velas, vigila stops, cobra funding,
  aplica el cierre diario, busca señales y las pasa por las dos carteras, aplica límites y guarda la curva.
- monitor (cada minuto): vigila stops/objetivos con el precio actual y aplica el cierre diario y los límites.
- ciclo_noticias (cada 30 min): lee noticias, las analiza con Claude, y si una de impacto alto afecta a una
  posición abierta de la cartera con Claude, le pregunta si hay que cerrarla.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.cartera import CARTERA_CLAUDE, CARTERA_SOLO, GestorCartera, ms
from bot.config import Config
from bot.datos.historico import cargar_velas, ms_temporalidad
from bot.db.modelos import LlamadaClaude, Noticia, Senal
from bot.diario import lecciones_json, texto_entrada
from bot.ia.cliente import ClienteClaude
from bot.ia.contexto import contexto_para_noticia, contexto_para_senal
from bot.ia.decision import ACCIONES_APERTURA, debe_consultar_por_noticia, decidir
from bot.noticias.analisis import analizar_pendientes, guardar_nuevas, noticias_recientes
from bot.noticias.filtro import base_de
from bot.noticias.fuentes import descargar_fuente
from bot.noticias.impacto import medir_impactos
from bot.notificaciones import Notificador
from bot.riesgo import Propuesta, evaluar_apertura
from bot.senales import NINGUNA, ParametrosSenal, extraer_candidatas, generar_senales, guardar_senales

log = logging.getLogger(__name__)
VELAS_CALCULO = 400  # velas que se cargan para calcular indicadores (sobra para EMA 100)


class Mercado(Protocol):
    def actualizar(self, ahora: pd.Timestamp) -> None: ...
    def precios(self) -> dict[str, float]: ...


@dataclass
class Lecciones:
    """Proveedor de lecciones vigentes (se conecta al sistema de aprendizaje en la Fase 6)."""
    def vigentes(self) -> list[dict]:
        return []


class Ciclo:
    def __init__(self, sesion: Session, config: Config, mercado: Mercado, carteras: dict[str, GestorCartera],
                 cliente_claude: ClienteClaude | None, avisos: Notificador, lecciones: Lecciones | None = None,
                 lector_rss=descargar_fuente):
        self.s = sesion
        self.config = config
        self.mercado = mercado
        self.carteras = carteras
        self.claude = cliente_claude
        self.avisos = avisos
        self.lecciones = lecciones or Lecciones()
        self.lector_rss = lector_rss
        self.tf_ms = ms_temporalidad(config.temporalidad)

    # ------------------------------------------------------------------ utilidades
    def _despues_del_cierre(self, ahora: pd.Timestamp) -> bool:
        hh, mm = map(int, self.config.riesgo.hora_cierre_diario_utc.split(":"))
        return ahora.hour * 60 + ahora.minute >= hh * 60 + mm

    def _cierre_diario(self, precios: dict[str, float], ahora: pd.Timestamp) -> None:
        if self._despues_del_cierre(ahora):
            for g in self.carteras.values():
                if g.abiertas():
                    g.cerrar_todo(precios, "cierre_diario", ahora)

    def _extremos_ultima_vela(self, velas: dict[str, pd.DataFrame]) -> dict[str, tuple[float, float]]:
        return {par: (float(df["high"].iloc[-1]), float(df["low"].iloc[-1])) for par, df in velas.items() if not df.empty}

    def _parametros(self) -> ParametrosSenal:
        return ParametrosSenal.desde_config(self.config)

    # ------------------------------------------------------------------ ciclo principal
    def ciclo_horario(self, ahora: pd.Timestamp) -> None:
        self.mercado.actualizar(ahora)
        precios = self.mercado.precios()
        p = self._parametros()
        ultima_cerrada = ahora.floor("h") - pd.Timedelta(milliseconds=self.tf_ms)
        velas, senales = {}, {}
        for par in self.config.pares:
            df = cargar_velas(self.s, self.config.exchange.nombre, par, self.config.temporalidad,
                              desde_ms=ms(ahora) - VELAS_CALCULO * self.tf_ms, hasta_ms=ms(ahora.floor("h")))
            if df.empty:
                continue
            velas[par] = df
            senales[par] = generar_senales(df, p, self.config.temporalidad)

        for g in self.carteras.values():
            g.cobrar_funding(precios, ahora)
            g.vigilar(precios, self._extremos_ultima_vela(velas), ahora)
        self._cierre_diario(precios, ahora)

        candidatas = []
        for par, df in senales.items():
            if df.index[-1] != ultima_cerrada:
                log.warning("%s: la última vela (%s) no es la recién cerrada (%s); se omite", par, df.index[-1], ultima_cerrada)
                continue
            guardar_senales(self.s, df.tail(1), self.config.exchange.nombre, par, p, self.config.temporalidad)
            if df["senal"].iloc[-1] != NINGUNA:
                candidatas += [(c, df) for c in extraer_candidatas(df.tail(1), par, p, self.config.temporalidad)]
        candidatas.sort(key=lambda x: -x[0].vol_rel)  # más volumen primero (si no caben todas)
        for senal, df in candidatas:
            precio = precios.get(senal.par)
            if precio is None:
                continue
            senal_id = self.s.scalar(select(Senal.id).where(
                Senal.par == senal.par, Senal.ts == ms(senal.ts_vela), Senal.estado == "confirmada").order_by(Senal.id.desc()))
            if CARTERA_SOLO in self.carteras:
                self._operar_solo(senal, precio, precios, ahora, senal_id)
            if CARTERA_CLAUDE in self.carteras:
                self._operar_con_claude(senal, df, precio, precios, ahora, senal_id)

        for g in self.carteras.values():
            g.aplicar_limites(precios, ahora)
            g.foto_capital(precios, ahora)

    def _propuesta_desde_senal(self, cartera: str, senal, precio: float, **extra) -> Propuesta:
        d = 1 if senal.direccion == "largo" else -1
        dist_sl = abs(senal.precio_referencia - senal.stop_loss)
        dist_tp = abs(senal.take_profit - senal.precio_referencia)
        return Propuesta(cartera=cartera, par=senal.par, direccion=senal.direccion, precio_entrada=precio,
                         stop_loss=precio - d * dist_sl, take_profit=precio + d * dist_tp, **extra)

    def _operar_solo(self, senal, precio, precios, ahora, senal_id) -> None:
        g = self.carteras[CARTERA_SOLO]
        prop = self._propuesta_desde_senal(CARTERA_SOLO, senal, precio, origen="senal_tecnica")
        v = evaluar_apertura(prop, g.estado_riesgo(precios, ahora), self.config, ahora)
        diario = texto_entrada(
            explicacion_senal=senal.explicar(), decision=None, reglas_cumplidas=v.reglas_cumplidas,
            precio=precio, stop=prop.stop_loss, objetivo=prop.take_profit,
            riesgo_usd=v.tamano.riesgo_usd if v.tamano else 0, nocional=v.tamano.nocional if v.tamano else 0,
            cartera=CARTERA_SOLO)
        g.abrir(prop, v, ts_senal=senal.ts_vela, ahora=ahora, diario=diario, senal_id=senal_id)

    def _operar_con_claude(self, senal, df, precio, precios, ahora, senal_id) -> None:
        g = self.carteras[CARTERA_CLAUDE]
        if senal_id is not None and self.s.scalar(select(LlamadaClaude.id).where(
                LlamadaClaude.senal_id == senal_id, LlamadaClaude.proposito == "decision")):
            log.info("La señal %s ya fue consultada a Claude: no se paga dos veces", senal_id)
            return
        estado = g.estado_riesgo(precios, ahora)
        # si la cartera no puede operar, no se gasta dinero en consultar a Claude
        prev = evaluar_apertura(self._propuesta_desde_senal(CARTERA_CLAUDE, senal, precio, origen="senal_tecnica"),
                                estado, self.config, ahora)
        bloqueos_previos = [b for b in prev.bloqueos if b.split(":")[0] in ("R1", "R2", "R3", "R4", "R5", "R6")]
        if bloqueos_previos:
            g.evento("bloqueo", "Sin consultar a Claude: " + " | ".join(bloqueos_previos), par=senal.par,
                     senal_id=senal_id, ahora=ahora)
            return
        if self.claude is None:
            g.evento("bloqueo", "Claude no configurado (falta ANTHROPIC_API_KEY): la cartera con Claude no opera",
                     par=senal.par, senal_id=senal_id, ahora=ahora)
            return
        posiciones = [{"par": o.par, "direccion": o.direccion, "entrada": o.precio_entrada, "stop": o.stop_loss}
                      for o in g.abiertas()]
        lecciones = self.lecciones.vigentes()
        ctx = contexto_para_senal(self.s, self.config, senal, df, ahora, posiciones, estado.capital,
                                  estado.capital - sum(estado.posiciones.values()), lecciones)
        r = decidir(self.claude, ctx, self.config.claude.esfuerzo_decision, senal_id=senal_id)
        llamada_id = r.llamada.id
        if r.decision is not None and r.decision.accion not in ACCIONES_APERTURA and not r.problemas:
            g.evento("rechazada_por_claude", f"Claude eligió '{r.decision.accion}' (confianza {r.decision.confianza:.2f}): "
                     f"{r.decision.razonamiento}", par=senal.par, senal_id=senal_id, llamada_id=llamada_id, ahora=ahora)
            return
        d = r.decision
        prop = Propuesta(
            cartera=CARTERA_CLAUDE, par=senal.par, direccion=senal.direccion, precio_entrada=precio,
            stop_loss=d.stop_loss if d else 0.0, take_profit=d.take_profit if d else 0.0,
            origen="senal_tecnica+claude", confianza=d.confianza if d else None,
            tamano_pct=d.tamano_sugerido_pct if d else 0.0, problemas_previos=r.problemas,
        )
        if d is not None and not r.problemas:
            # Claude fija SL/TP sobre el precio de la señal; se trasladan al precio actual manteniendo distancias
            dir_ = 1 if senal.direccion == "largo" else -1
            prop.stop_loss = precio - dir_ * abs(senal.precio_referencia - d.stop_loss)
            prop.take_profit = precio + dir_ * abs(d.take_profit - senal.precio_referencia)
        v = evaluar_apertura(prop, estado, self.config, ahora)
        base = senal.par.split("/")[0]
        noticias = noticias_recientes(self.s, base, ms(ahora) - self.config.noticias.horas_contexto * 3_600_000, ms(ahora))
        diario = texto_entrada(
            explicacion_senal=senal.explicar(), decision=d, noticias=noticias, reglas_cumplidas=v.reglas_cumplidas,
            precio=precio, stop=prop.stop_loss, objetivo=prop.take_profit,
            riesgo_usd=v.tamano.riesgo_usd if v.tamano else 0, nocional=v.tamano.nocional if v.tamano else 0,
            cartera=CARTERA_CLAUDE)
        g.abrir(prop, v, ts_senal=senal.ts_vela, ahora=ahora, diario=diario, senal_id=senal_id, llamada_id=llamada_id,
                lecciones=lecciones_json(d))

    # ------------------------------------------------------------------ monitor
    def monitor(self, ahora: pd.Timestamp) -> None:
        if not any(g.abiertas() for g in self.carteras.values()):
            return
        precios = self.mercado.precios()
        for g in self.carteras.values():
            g.vigilar(precios, {}, ahora)
        self._cierre_diario(precios, ahora)
        for g in self.carteras.values():
            g.aplicar_limites(precios, ahora)

    # ------------------------------------------------------------------ noticias
    def ciclo_noticias(self, ahora: pd.Timestamp) -> None:
        bases = [base_de(p) for p in self.config.pares]
        for url in self.config.noticias.fuentes_rss:
            guardar_nuevas(self.s, self.lector_rss(url, ms(ahora)), bases, ms(ahora))
        altas: list[Noticia] = []
        if self.claude is not None:
            altas = analizar_pendientes(self.s, self.claude, bases, ms(ahora), self.config.claude.max_noticias_por_llamada,
                                        self.config.claude.esfuerzo_noticias)
        medir_impactos(self.s, self.config.exchange.nombre, self.config.pares, ms(ahora))
        if not altas or CARTERA_CLAUDE not in self.carteras:
            return
        g = self.carteras[CARTERA_CLAUDE]
        precios = self.mercado.precios()
        for n in altas:
            self.avisos.enviar(f"📰 Noticia de impacto ALTO ({n.tema}, sentimiento {n.sentimiento:+.2f}): {n.resumen}")
            posiciones = [{"par": o.par, "direccion": o.direccion, "entrada": o.precio_entrada, "stop": o.stop_loss}
                          for o in g.abiertas()]
            for par in debe_consultar_por_noticia(n, posiciones):
                estado = g.estado_riesgo(precios, ahora)
                df = cargar_velas(self.s, self.config.exchange.nombre, par, self.config.temporalidad,
                                  desde_ms=ms(ahora) - VELAS_CALCULO * self.tf_ms)
                df = generar_senales(df, self._parametros(), self.config.temporalidad) if not df.empty else None
                ctx = contexto_para_noticia(self.s, self.config, n, par, precios.get(par, 0.0), df, ahora, posiciones,
                                            estado.capital, estado.capital - sum(estado.posiciones.values()),
                                            self.lecciones.vigentes())
                r = decidir(self.claude, ctx, self.config.claude.esfuerzo_decision)
                if r.decision is not None and r.decision.accion == "cerrar" and not r.problemas:
                    op = next((o for o in g.abiertas() if o.par == par), None)
                    if op is not None and par in precios:
                        g.cerrar(op, precios[par], "claude_noticia", ahora)
                        op.analisis_post += f"\n- Cerrada por noticia: {n.titulo}. Claude: {r.decision.razonamiento}"
                        self.s.commit()
                else:
                    g.evento("noticia_sin_accion", f"Noticia '{n.titulo}': Claude no pidió cerrar {par}"
                             + (f" ({'; '.join(r.problemas)})" if r.problemas else ""), par=par,
                             llamada_id=r.llamada.id, ahora=ahora)
