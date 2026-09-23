"""Motor de aprendizaje.

Ciclo de vida de una hipótesis (nunca toca dinero real):

  propuesta ──(confirmación en histórico no usado para encontrarla)──► en_prueba ──(datos NUEVOS, posteriores a
      │                                                                    │      su creación)──► validada ─► LECCIÓN
      └──────────── no pasa ──► descartada ◄──────────── no pasa ─────────┘

  - Exploración: las hipótesis estadísticas se buscan en el 60% más antiguo del histórico.
  - Confirmación: se prueban en el 40% más reciente anterior a su creación (el buscador no lo vio).
  - Hacia adelante: solo con datos generados después de crearla (imposible haberlos visto).
  Las hipótesis sobre datos de Claude (p. ej. su confianza) no tienen histórico: se prueban solo hacia adelante con
  las operaciones reales del paper trading.

Lecciones: vigente ─(datos nuevos más débiles)─► en_revision ─(fallan otra vez o el efecto se invierte)─► refutada
(se revierte su ajuste y se explica por qué). Una lección puede volver de en_revision a vigente si se confirma.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bot.aprendizaje.ajustes import AjusteInvalido, aplicar_ajuste, config_efectiva, revertir_ajuste, validar_ajuste
from bot.aprendizaje.condiciones import Condicion, a_json, desde_json, describir, mascara, solo_paper
from bot.aprendizaje.estadistica import ResultadoPrueba, comparar
from bot.aprendizaje.muestras import muestras_paper, muestras_simuladas
from bot.backtest.motor import ParametrosBacktest
from bot.cartera import CARTERA_CLAUDE
from bot.config import Config
from bot.datos.historico import cargar_velas
from bot.db.modelos import AjusteParametro, HistorialAprendizaje, Hipotesis, Leccion
from bot.senales import ParametrosSenal, generar_senales

log = logging.getLogger(__name__)

FRACCION_EXPLORACION = 0.6
DIAS_SIN_REPROPONER = 90
MIN_EXPLORACION = 20          # casos mínimos en exploración para proponer algo
DIFERENCIA_EXPLORACION = 0.15  # diferencia mínima (R) en exploración para que valga la pena probarla
PASOS_PARAMETROS = {"multiplicador_volumen": 0.5, "atr_mult_sl": 0.5, "ratio_tp": 0.5, "rsi_sobrecompra": 5.0,
                    "rsi_compra_min": 5.0}


def ms(ts: pd.Timestamp) -> int:
    return int(ts.timestamp() * 1000)


def ts(ms_: int) -> pd.Timestamp:
    return pd.Timestamp(ms_, unit="ms", tz="UTC")


def firma_de(tipo: str, condiciones: list[Condicion], parametro: str | None, valor: float | None, efecto: str) -> str:
    if tipo == "parametro":
        return f"parametro:{parametro}={float(valor):g}"
    return f"filtro:{efecto}:{a_json(condiciones)}"[:200]


def candidatos_filtro(pares: list[str]) -> list[list[Condicion]]:
    C = Condicion
    lista = [
        [C(campo="vol_rel", op=">=", valor=2.0)], [C(campo="vol_rel", op=">=", valor=2.5)],
        [C(campo="direccion", op="==", valor="largo")], [C(campo="direccion", op="==", valor="corto")],
        [C(campo="hora_utc", op="<", valor=8)], [C(campo="hora_utc", op=">=", valor=13), C(campo="hora_utc", op="<", valor=21)],
        [C(campo="dia_semana", op=">=", valor=5)],
        [C(campo="atr_pct", op=">=", valor=1.0)], [C(campo="atr_pct", op="<", valor=0.5)],
        [C(campo="direccion", op="==", valor="largo"), C(campo="rsi", op=">=", valor=60)],
        [C(campo="direccion", op="==", valor="corto"), C(campo="rsi", op="<=", valor=40)],
    ]
    lista += [[C(campo="par", op="==", valor=p)] for p in pares]
    return lista


@dataclass
class InformeSemanal:
    nuevas: list[str] = field(default_factory=list)
    cambios_estado: list[str] = field(default_factory=list)
    lecciones_nuevas: list[str] = field(default_factory=list)
    revisiones: list[str] = field(default_factory=list)
    ajustes: list[str] = field(default_factory=list)
    comentario_claude: str = ""

    def texto(self) -> str:
        L = ["# Revisión semanal de aprendizaje", ""]
        for titulo, items in (("Hipótesis nuevas", self.nuevas), ("Cambios de estado", self.cambios_estado),
                              ("Lecciones nuevas", self.lecciones_nuevas), ("Revalidación de lecciones", self.revisiones),
                              ("Ajustes de parámetros", self.ajustes)):
            L += [f"## {titulo}", *(f"- {i}" for i in items)] if items else [f"## {titulo}", "- (ninguno)"]
            L.append("")
        if self.comentario_claude:
            L += ["## Comentario de Claude", self.comentario_claude, ""]
        return "\n".join(L)

    def resumen_corto(self) -> str:
        return (f"🧠 Aprendizaje semanal: {len(self.nuevas)} hipótesis nuevas, {len(self.lecciones_nuevas)} lecciones "
                f"nuevas, {len(self.cambios_estado)} cambios de estado, {len(self.ajustes)} ajustes.")


class MotorAprendizaje:
    def __init__(self, sesion: Session, config_base: Config, ahora: pd.Timestamp, velas_por_par: dict | None = None):
        self.s = sesion
        self.base = config_base
        self.ahora = ahora
        self.velas = velas_por_par if velas_por_par is not None else self._cargar_velas()
        self._cache: dict[ParametrosSenal, dict] = {}

    # ------------------------------------------------------------------ datos
    def _cargar_velas(self) -> dict[str, pd.DataFrame]:
        velas = {}
        for par in self.base.pares:
            df = cargar_velas(self.s, self.base.exchange.nombre, par, self.base.temporalidad, hasta_ms=ms(self.ahora))
            if not df.empty:
                velas[par] = df
        return velas

    @property
    def config(self) -> Config:
        return config_efectiva(self.base, self.s)

    def _pb(self) -> ParametrosBacktest:
        return ParametrosBacktest.desde_config(self.base)

    def _senales(self, config: Config) -> dict[str, pd.DataFrame]:
        p = ParametrosSenal.desde_config(config)
        if p not in self._cache:
            self._cache[p] = {par: generar_senales(df, p, config.temporalidad, con_motivos=False)
                              for par, df in self.velas.items()}
        return self._cache[p]

    def _inicio_datos(self) -> pd.Timestamp:
        inicio = min(df.index[0] for df in self.velas.values())
        return inicio + pd.Timedelta(hours=300)  # calentamiento de indicadores

    def corte_exploracion(self, hasta: pd.Timestamp) -> pd.Timestamp:
        inicio = self._inicio_datos()
        return inicio + (hasta - inicio) * FRACCION_EXPLORACION

    def muestras_exploracion(self) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
        """Operaciones simuladas del tramo de exploración (lo único que ven el buscador estadístico y Claude)."""
        inicio, corte = self._inicio_datos(), self.corte_exploracion(self.ahora)
        return muestras_simuladas(self._senales(self.config), self._pb(), inicio, corte), inicio, corte

    def _con_valor(self, config: Config, parametro: str, valor: float) -> Config:
        datos = config.model_dump()
        datos["estrategia"][parametro]["valor"] = valor
        return Config.model_validate(datos)

    # ------------------------------------------------------------------ registro
    def _historial(self, entidad: str, entidad_id: int | None, evento: str, detalle: str) -> None:
        self.s.add(HistorialAprendizaje(ts_ms=ms(self.ahora), entidad=entidad, entidad_id=entidad_id, evento=evento,
                                        detalle=detalle))

    def _siguiente_codigo(self, modelo, prefijo: str) -> str:
        return f"{prefijo}{(self.s.scalar(select(func.count(modelo.id))) or 0) + 1:04d}"

    # ------------------------------------------------------------------ creación
    def crear_hipotesis(self, *, origen: str, tipo: str, enunciado: str, explicacion: str, efecto: str,
                        condiciones: list[Condicion] | None = None, parametro: str | None = None,
                        valor: float | None = None) -> Hipotesis | None:
        condiciones = condiciones or []
        if tipo == "filtro" and not condiciones:
            self._historial("hipotesis", None, "propuesta_rechazada", f"'{enunciado}': un filtro necesita condiciones")
            return None
        if tipo == "parametro":
            try:
                validar_ajuste(self.config, parametro, float(valor))
            except (AjusteInvalido, TypeError, ValueError) as e:
                self._historial("hipotesis", None, "propuesta_rechazada", f"'{enunciado}': {e}")
                return None
        firma = firma_de(tipo, condiciones, parametro, valor, efecto)
        limite = ms(self.ahora - pd.Timedelta(days=DIAS_SIN_REPROPONER))
        previa = self.s.scalar(select(Hipotesis).where(Hipotesis.firma == firma).order_by(Hipotesis.id.desc()))
        if previa is not None and (previa.estado != "descartada" or previa.actualizada_ms >= limite):
            return None
        h = Hipotesis(
            codigo=self._siguiente_codigo(Hipotesis, "H"), enunciado=enunciado, explicacion=explicacion, origen=origen,
            tipo=tipo, condiciones=a_json(condiciones), parametro=parametro,
            valor_propuesto=float(valor) if valor is not None else None, efecto_esperado=efecto,
            muestra_minima=30, estado="propuesta", creada_ms=ms(self.ahora), actualizada_ms=ms(self.ahora), firma=firma,
        )
        self.s.add(h)
        self.s.flush()
        self._historial("hipotesis", h.id, "creada", f"[{origen}] {enunciado}")
        self.s.commit()
        return h

    def proponer_estadisticas(self, max_filtros: int = 5, max_parametros: int = 2) -> list[Hipotesis]:
        """Busca patrones SOLO en el tramo de exploración y los convierte en hipótesis por confirmar."""
        if not self.velas:
            return []
        base, inicio, corte = self.muestras_exploracion()
        config = self.config
        creadas = []
        encontrados = []
        for conds in candidatos_filtro(list(self.velas)):
            m = mascara(conds, base)
            g, c = base[m], base[~m]
            if len(g) < MIN_EXPLORACION or len(c) < MIN_EXPLORACION:
                continue
            diff = g["r_multiple"].mean() - c["r_multiple"].mean()
            if abs(diff) >= DIFERENCIA_EXPLORACION:
                encontrados.append((abs(diff), conds, g, c, diff))
        for _, conds, g, c, diff in sorted(encontrados, key=lambda x: -x[0]):
            if len([h for h in creadas if h.tipo == "filtro"]) >= max_filtros:
                break
            efecto = "mejor" if diff > 0 else "peor"
            h = self.crear_hipotesis(
                origen="estadistico", tipo="filtro", efecto=efecto, condiciones=conds,
                enunciado=f"Las operaciones con {describir(conds)} rinden {efecto} que el resto",
                explicacion=(f"En la exploración ({inicio:%Y-%m-%d} a {corte:%Y-%m-%d}) tuvieron R medio "
                             f"{g['r_multiple'].mean():+.2f} frente a {c['r_multiple'].mean():+.2f} del resto "
                             f"({len(g)} vs {len(c)} operaciones). Puede ser casualidad: falta confirmarlo con datos "
                             "que no se usaron para encontrarlo."))
            if h:
                creadas.append(h)
        # parámetros vecinos (siempre dentro de los rangos del dueño)
        r_actual = base["r_multiple"].mean() if len(base) >= 30 else None
        mejoras = []
        for nombre, paso in PASOS_PARAMETROS.items():
            rango = getattr(config.estrategia, nombre)
            for valor in (rango.valor - paso, rango.valor + paso):
                if r_actual is None or not rango.min <= valor <= rango.max:
                    continue
                try:
                    cfg = self._con_valor(config, nombre, valor)
                except Exception:  # noqa: BLE001 - combinación inválida (p. ej. zonas de RSI cruzadas)
                    continue
                alt = muestras_simuladas(self._senales(cfg), self._pb(), inicio, corte)
                if len(alt) >= 30 and alt["r_multiple"].mean() - r_actual >= DIFERENCIA_EXPLORACION:
                    mejoras.append((alt["r_multiple"].mean() - r_actual, nombre, valor, len(alt), alt["r_multiple"].mean()))
        for _diff, nombre, valor, n, r in sorted(mejoras, reverse=True)[:max_parametros]:
            actual = getattr(config.estrategia, nombre).valor
            h = self.crear_hipotesis(
                origen="estadistico", tipo="parametro", efecto="mejor", parametro=nombre, valor=valor,
                enunciado=f"Cambiar {nombre} de {actual:g} a {valor:g} mejora el resultado por operación",
                explicacion=(f"En la exploración, con {nombre}={valor:g} el R medio fue {r:+.2f} ({n} operaciones) "
                             f"frente a {r_actual:+.2f} con el valor actual. Falta confirmarlo con otros datos."))
            if h:
                creadas.append(h)
        return creadas

    # ------------------------------------------------------------------ pruebas
    def _muestras(self, h: Hipotesis, desde: pd.Timestamp, hasta: pd.Timestamp, config: Config | None = None
                  ) -> tuple[pd.DataFrame, pd.DataFrame, str, str]:
        config = config or self.config
        if h.tipo == "parametro":
            control = muestras_simuladas(self._senales(config), self._pb(), desde, hasta)
            actual = getattr(config.estrategia, h.parametro).valor
            if actual == h.valor_propuesto:  # ya aplicado: se compara contra el valor anterior
                ajuste = self.s.scalar(select(AjusteParametro).where(AjusteParametro.parametro == h.parametro)
                                       .order_by(AjusteParametro.id.desc()))
                actual = ajuste.valor_anterior if ajuste else actual
                control = muestras_simuladas(self._senales(self._con_valor(config, h.parametro, actual)), self._pb(),
                                             desde, hasta)
            grupo = muestras_simuladas(self._senales(self._con_valor(config, h.parametro, h.valor_propuesto)),
                                       self._pb(), desde, hasta)
            return grupo, control, f"con {h.parametro}={h.valor_propuesto:g}", f"con {h.parametro}={actual:g}"
        conds = desde_json(h.condiciones)
        if solo_paper(conds):
            base = muestras_paper(self.s, CARTERA_CLAUDE, ms(desde))
            base = base[base["ts_entrada"] < hasta]
        else:
            base = muestras_simuladas(self._senales(config), self._pb(), desde, hasta)
        m = mascara(conds, base)
        return base[m], base[~m], f"operaciones con {describir(conds)}", "el resto"

    def _probar(self, h: Hipotesis, desde: pd.Timestamp, hasta: pd.Timestamp) -> ResultadoPrueba:
        g, c, ng, nc = self._muestras(h, desde, hasta)
        return comparar(g, c, h.efecto_esperado, h.muestra_minima, ng, nc)

    def _cambiar(self, h: Hipotesis, estado: str, motivo: str, informe: InformeSemanal) -> None:
        h.estado = estado
        h.motivo_estado = motivo
        h.actualizada_ms = ms(self.ahora)
        self._historial("hipotesis", h.id, estado, motivo)
        informe.cambios_estado.append(f"{h.codigo} → {estado}: {h.enunciado}. {motivo}")

    def probar_hipotesis(self, h: Hipotesis, informe: InformeSemanal) -> None:
        creada = ts(h.creada_ms)
        if h.estado == "propuesta":
            if h.tipo == "filtro" and solo_paper(desde_json(h.condiciones)):
                self._cambiar(h, "en_prueba", "Usa datos de Claude, que no existen en el histórico: se probará solo "
                              "con operaciones del paper trading posteriores a su creación.", informe)
            else:
                r = self._probar(h, self.corte_exploracion(creada), creada)
                h.evidencia_backtest = json.dumps(r.a_dict(), ensure_ascii=False)
                if r.pasa:
                    self._cambiar(h, "en_prueba", "Confirmada en el histórico no usado para encontrarla. "
                                  f"{r.explicacion} Ahora falta que se cumpla con datos nuevos.", informe)
                elif not r.suficiente:
                    self._cambiar(h, "descartada", f"No hay casos suficientes en el histórico para confirmarla. {r.explicacion}", informe)
                else:
                    self._cambiar(h, "descartada", f"No se confirmó en el histórico no usado para encontrarla. {r.explicacion}", informe)
            self.s.commit()
            return
        if h.estado == "en_prueba":
            r = self._probar(h, creada, self.ahora)
            h.evidencia_adelante = json.dumps(r.a_dict(), ensure_ascii=False)
            if not r.suficiente:
                h.motivo_estado = f"Esperando datos nuevos. {r.explicacion}"
            elif r.pasa:
                self._cambiar(h, "validada", f"Se cumplió con datos nuevos. {r.explicacion}", informe)
                self._crear_leccion(h, r, informe)
            else:
                self._cambiar(h, "descartada", f"No se cumplió con datos nuevos. {r.explicacion}", informe)
            self.s.commit()

    def _crear_leccion(self, h: Hipotesis, r: ResultadoPrueba, informe: InformeSemanal) -> Leccion:
        evidencia = {"backtest": json.loads(h.evidencia_backtest) if h.evidencia_backtest else None,
                     "adelante": r.a_dict()}
        lec = Leccion(codigo=self._siguiente_codigo(Leccion, "L"), hipotesis_id=h.id, enunciado=h.enunciado,
                      explicacion=f"{h.explicacion}\n\nEvidencia con datos nuevos: {r.explicacion}", evidencia=json.dumps(
                          evidencia, ensure_ascii=False), estado="vigente", validada_ms=ms(self.ahora))
        self.s.add(lec)
        self.s.flush()
        self._historial("leccion", lec.id, "creada", f"De {h.codigo}: {h.enunciado}")
        informe.lecciones_nuevas.append(f"{lec.codigo}: {lec.enunciado}")
        if h.tipo == "parametro":
            # una lección anterior que ajustaba el mismo parámetro queda reemplazada
            for vieja in self.s.scalars(select(Leccion).where(Leccion.estado.in_(("vigente", "en_revision")),
                                                              Leccion.ajuste_id.is_not(None), Leccion.id != lec.id)):
                aj = self.s.get(AjusteParametro, vieja.ajuste_id)
                if aj and aj.parametro == h.parametro:
                    vieja.estado = "reemplazada"
                    vieja.motivo_estado = f"Reemplazada por {lec.codigo}"
                    self._historial("leccion", vieja.id, "reemplazada", f"Reemplazada por {lec.codigo}")
            try:
                aj = aplicar_ajuste(self.s, self.base, h.parametro, h.valor_propuesto,
                                    f"Lección {lec.codigo}: {r.explicacion}", ms(self.ahora), leccion_id=lec.id)
                lec.ajuste_id = aj.id
                informe.ajustes.append(f"{h.parametro}: {aj.valor_anterior:g} → {aj.valor_nuevo:g} (lección {lec.codigo})")
            except AjusteInvalido as e:
                lec.motivo_estado = f"No se pudo aplicar el ajuste: {e}"
                self._historial("leccion", lec.id, "ajuste_no_aplicado", str(e))
        self.s.commit()
        return lec

    def revalidar(self, lec: Leccion, informe: InformeSemanal) -> None:
        h = self.s.get(Hipotesis, lec.hipotesis_id)
        r = self._probar(h, ts(lec.validada_ms), self.ahora)
        lec.revisada_ms = ms(self.ahora)
        if not r.suficiente:
            self.s.commit()
            return
        signo = 1 if h.efecto_esperado == "mejor" else -1
        anterior = lec.estado
        if r.pasa:
            if anterior == "en_revision":
                lec.estado, lec.motivo_estado = "vigente", f"Se volvió a confirmar con datos nuevos. {r.explicacion}"
        elif r.diferencia_r * signo > 0 and anterior == "vigente":
            lec.estado = "en_revision"
            lec.motivo_estado = (f"Con los datos posteriores a su validación el efecto sigue en la misma dirección pero "
                                 f"más débil. {r.explicacion} Se vigila una semana más antes de decidir.")
        else:
            lec.estado = "refutada"
            causa = ("el efecto se invirtió" if r.diferencia_r * signo <= 0
                     else "siguió sin confirmarse tras estar en revisión")
            lec.motivo_estado = f"Refutada: con datos nuevos {causa}. {r.explicacion}"
            if lec.ajuste_id:
                aj = self.s.get(AjusteParametro, lec.ajuste_id)
                if aj and aj.revertido_ms is None:
                    revertir_ajuste(self.s, aj.id, f"Lección {lec.codigo} refutada: {causa}.", ms(self.ahora))
                    informe.ajustes.append(f"REVERTIDO {aj.parametro}: vuelve a {aj.valor_anterior:g} (lección {lec.codigo} refutada)")
        if lec.estado != anterior:
            self._historial("leccion", lec.id, lec.estado, lec.motivo_estado)
            informe.revisiones.append(f"{lec.codigo} {anterior} → {lec.estado}: {lec.motivo_estado}")
        self.s.commit()

    # ------------------------------------------------------------------ ciclo completo
    def ciclo_semanal(self, revisor=None) -> InformeSemanal:
        """1) proponer (estadística y Claude)  2) probar hipótesis  3) revalidar lecciones."""
        informe = InformeSemanal()
        for h in self.proponer_estadisticas():
            informe.nuevas.append(f"{h.codigo} [estadístico] {h.enunciado}")
        if revisor is not None:
            for h, _texto in revisor.revisar(self):
                if h is not None:
                    informe.nuevas.append(f"{h.codigo} [Claude] {h.enunciado}")
            informe.comentario_claude = revisor.ultimo_comentario
        for h in self.s.scalars(select(Hipotesis).where(Hipotesis.estado.in_(("propuesta", "en_prueba")))).all():
            estado_previo = h.estado
            self.probar_hipotesis(h, informe)
            if estado_previo == "propuesta" and h.estado == "en_prueba" and not solo_paper(desde_json(h.condiciones)):
                self.probar_hipotesis(h, informe)  # por si ya hay datos nuevos suficientes
        for lec in self.s.scalars(select(Leccion).where(Leccion.estado.in_(("vigente", "en_revision")))).all():
            self.revalidar(lec, informe)
        self._historial("revision", None, "semanal", informe.resumen_corto())
        self.s.commit()
        return informe


class ProveedorLecciones:
    """Lecciones vigentes que se pasan a Claude en cada decisión."""

    def __init__(self, sesion: Session):
        self.s = sesion

    def vigentes(self) -> list[dict]:
        salida = []
        for lec in self.s.scalars(select(Leccion).where(Leccion.estado == "vigente").order_by(Leccion.id)):
            ev = json.loads(lec.evidencia).get("adelante") or {}
            salida.append({"id": lec.codigo, "enunciado": (
                f"{lec.enunciado} (validada con {ev.get('n_grupo', '?')} operaciones: R medio "
                f"{ev.get('r_medio_grupo', 0):+.2f} vs {ev.get('r_medio_control', 0):+.2f} del control)")})
        return salida
