"""Revisión semanal con Claude: propone hipótesis nuevas y comenta las existentes.

Claude solo PROPONE. Sus hipótesis entran como "propuesta" y deben pasar exactamente las mismas pruebas
estadísticas que las demás. No puede validar, refutar ni cambiar parámetros por sí mismo.
Solo ve estadísticas del tramo de EXPLORACIÓN y del paper trading, nunca del tramo de confirmación.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from bot.aprendizaje.condiciones import Condicion, describir, mascara
from bot.aprendizaje.muestras import muestras_paper
from bot.cartera import CARTERA_CLAUDE, CARTERA_SOLO
from bot.db.modelos import HistorialAprendizaje, Hipotesis, Leccion
from bot.ia.cliente import ClienteClaude

MAX_NUEVAS = 5


class HipotesisPropuesta(BaseModel):
    enunciado: str = Field(description="Afirmación comprobable, en español")
    explicacion: str = Field(description="Por qué podría ser cierta, en lenguaje claro para alguien que aprende")
    tipo: Literal["filtro", "parametro"]
    condiciones: list[Condicion] = Field(description="Para tipo filtro: condiciones que definen el grupo (vacío si parametro)")
    parametro: str | None = Field(description="Para tipo parametro: nombre exacto del parámetro ajustable")
    valor_propuesto: float | None = Field(description="Para tipo parametro: nuevo valor, dentro de su rango")
    efecto_esperado: Literal["mejor", "peor"] = Field(description="¿El grupo rinde mejor o peor que el resto?")


class ComentarioExistente(BaseModel):
    codigo: str
    comentario: str
    recomendacion: Literal["mantener", "vigilar", "revisar"]


class RevisionSemanal(BaseModel):
    nuevas_hipotesis: list[HipotesisPropuesta]
    comentarios: list[ComentarioExistente]
    resumen_para_humano: str = Field(description="Qué aprendió el bot esta semana, en español claro, 5-8 frases")


SISTEMA_REVISION = """Eres el analista de aprendizaje de un bot de trading intradía de criptomonedas (velas de 1 h,
cierre diario, largos y cortos, riesgo fijo por operación). Cada semana recibes estadísticas de sus operaciones.

Tu trabajo: proponer como máximo 5 hipótesis NUEVAS y comprobables, y comentar las existentes.
Reglas:
- Una hipótesis de tipo "filtro" define un grupo de operaciones con condiciones sobre estos campos:
  par, direccion (largo/corto), hora_utc, dia_semana (0=lunes), vol_rel, rsi, atr_pct, confianza_claude.
- Una hipótesis de tipo "parametro" propone un nuevo valor para un parámetro ajustable, DENTRO de su rango.
- No propongas algo que ya existe en la lista. Prefiere ideas con buena muestra y una explicación de mercado sensata.
- Desconfía de diferencias con pocas operaciones: pueden ser suerte.
- No puedes validar nada: las hipótesis se prueban con datos que tú no ves.
- El resumen para el humano debe enseñar algo, sin prometer rentabilidad."""


class RevisorClaude:
    def __init__(self, cliente: ClienteClaude, esfuerzo: str = "medium"):
        self.cliente = cliente
        self.esfuerzo = esfuerzo
        self.ultimo_comentario = ""

    def contexto(self, motor) -> str:
        from bot.aprendizaje.motor import candidatos_filtro
        cfg = motor.config
        base, inicio, corte = motor.muestras_exploracion()
        filas = []
        for conds in candidatos_filtro(list(motor.velas)):
            m = mascara(conds, base)
            if m.sum() >= 10:
                filas.append(f"- {describir(conds)}: n={int(m.sum())}, R medio {base[m]['r_multiple'].mean():+.2f} "
                             f"vs resto {base[~m]['r_multiple'].mean():+.2f}")
        paper = []
        for cartera in (CARTERA_CLAUDE, CARTERA_SOLO):
            p = muestras_paper(motor.s, cartera)
            if len(p):
                paper.append(f"- {cartera}: {len(p)} operaciones, acierto {(p['r_multiple'] > 0).mean() * 100:.0f}%, "
                             f"R medio {p['r_multiple'].mean():+.2f}")
        pc = muestras_paper(motor.s, CARTERA_CLAUDE)
        if len(pc):
            for lo, hi in ((0, 0.65), (0.65, 0.75), (0.75, 1.01)):
                sub = pc[(pc["confianza_claude"] >= lo) & (pc["confianza_claude"] < hi)]
                if len(sub):
                    paper.append(f"  · confianza Claude {lo:.2f}-{min(hi, 1):.2f}: n={len(sub)}, R medio {sub['r_multiple'].mean():+.2f}")
        rangos = {k: v for k, v in cfg.estrategia.model_dump().items()}
        hips = [f"- {h.codigo} [{h.estado}] {h.enunciado}" for h in motor.s.scalars(select(Hipotesis).order_by(Hipotesis.id))]
        lecs = [f"- {l.codigo} [{l.estado}] {l.enunciado}" for l in motor.s.scalars(select(Leccion).order_by(Leccion.id))]
        return "\n".join([
            f"# Revisión semanal ({motor.ahora:%Y-%m-%d})",
            "## Parámetros ajustables (valor actual y rango permitido)", json.dumps(rangos, ensure_ascii=False),
            f"## Subgrupos en el tramo de exploración ({inicio:%Y-%m-%d} a {corte:%Y-%m-%d}, {len(base)} operaciones simuladas)",
            *(filas or ["(sin datos)"]), "## Paper trading", *(paper or ["(sin operaciones cerradas todavía)"]),
            "## Hipótesis existentes", *(hips or ["(ninguna)"]), "## Lecciones", *(lecs or ["(ninguna)"]),
        ])

    def revisar(self, motor) -> list[tuple]:
        r = self.cliente.consultar(proposito="aprendizaje", sistema=SISTEMA_REVISION, contenido=self.contexto(motor),
                                   esquema=RevisionSemanal, esfuerzo=self.esfuerzo, max_tokens=16000)
        if not r.ok:
            self.ultimo_comentario = f"(No hubo revisión de Claude: {r.llamada.estado})"
            return []
        rev: RevisionSemanal = r.datos
        self.ultimo_comentario = rev.resumen_para_humano
        for c in rev.comentarios:
            motor.s.add(HistorialAprendizaje(ts_ms=int(motor.ahora.timestamp() * 1000), entidad="revision",
                                             entidad_id=None, evento=f"claude_{c.recomendacion}",
                                             detalle=f"{c.codigo}: {c.comentario}"))
        creadas = []
        for p in rev.nuevas_hipotesis[:MAX_NUEVAS]:
            h = motor.crear_hipotesis(
                origen="claude", tipo=p.tipo, enunciado=p.enunciado, explicacion=p.explicacion, efecto=p.efecto_esperado,
                condiciones=p.condiciones if p.tipo == "filtro" else [], parametro=p.parametro, valor=p.valor_propuesto)
            creadas.append((h, p.enunciado))
        motor.s.commit()
        return creadas
