import json

import numpy as np
import pandas as pd
import pytest

import bot.aprendizaje.motor as motor_mod
from bot.aprendizaje.ajustes import AjusteInvalido, aplicar_ajuste, config_efectiva, revertir_ajuste
from bot.aprendizaje.condiciones import Condicion, a_json, desde_json, describir, mascara, solo_paper
from bot.aprendizaje.estadistica import comparar
from bot.aprendizaje.motor import InformeSemanal, MotorAprendizaje, ProveedorLecciones
from bot.aprendizaje.revision_claude import HipotesisPropuesta, RevisionSemanal, RevisorClaude, ComentarioExistente
from bot.db.modelos import AjusteParametro, HistorialAprendizaje, Hipotesis, Leccion
from bot.ia.cliente import ClienteClaude
from tests.claude_falso import ClienteFalso, respuesta

T0 = pd.Timestamp("2023-01-01", tz="UTC")
T1 = pd.Timestamp("2025-01-01", tz="UTC")          # momento de crear las hipótesis
INVERSION = pd.Timestamp("2025-04-01", tz="UTC")   # a partir de aquí el efecto plantado se invierte


# ---------------------------------------------------------------- estadística

def ops(r, inicio=T0, horas=6):
    return pd.DataFrame({"r_multiple": r, "ts_entrada": [inicio + pd.Timedelta(hours=horas * i) for i in range(len(r))]})


def ruido(n, media, semilla):
    return np.random.default_rng(semilla).normal(media, 1.0, n)


def test_efecto_real_pasa():
    r = comparar(ops(ruido(200, 0.6, 1)), ops(ruido(200, 0.0, 2)), "mejor", 30)
    assert r.pasa and r.suficiente and r.p_valor < 0.05 and r.robusta
    assert "PASA" in r.explicacion


def test_sin_efecto_no_pasa():
    r = comparar(ops(ruido(200, 0.0, 3)), ops(ruido(200, 0.0, 4)), "mejor", 30)
    assert not r.pasa and "NO PASA" in r.explicacion


def test_muestra_insuficiente():
    r = comparar(ops(ruido(20, 2.0, 5)), ops(ruido(200, 0.0, 6)), "mejor", 30)
    assert not r.suficiente and not r.pasa and "insuficiente" in r.explicacion


def test_efecto_solo_en_una_mitad_no_es_robusto():
    g = ops(np.concatenate([ruido(100, 1.5, 7), ruido(100, -0.3, 8)]))
    c = ops(ruido(200, 0.0, 9))
    r = comparar(g, c, "mejor", 30)
    assert r.robusta is False and not r.pasa


@pytest.mark.parametrize("media", [0.6, 0.05, 0.0, -0.6])
def test_resultado_siempre_serializable_a_json(media):
    r = comparar(ops(ruido(120, media, 12)), ops(ruido(120, 0.0, 13)), "mejor", 30)
    json.dumps(r.a_dict())
    assert all(type(getattr(r, c)) is bool for c in ("pasa", "suficiente", "robusta"))


def test_efecto_peor():
    assert comparar(ops(ruido(200, -0.6, 10)), ops(ruido(200, 0.0, 11)), "peor", 30).pasa


# ---------------------------------------------------------------- condiciones

def test_condiciones():
    df = pd.DataFrame({"vol_rel": [1.0, 2.5, 3.0], "direccion": ["largo", "corto", "largo"],
                       "confianza_claude": [np.nan, 0.8, 0.7]})
    conds = [Condicion(campo="vol_rel", op=">=", valor=2.0), Condicion(campo="direccion", op="==", valor="largo")]
    assert mascara(conds, df).tolist() == [False, False, True]
    assert describir(conds) == "volumen relativo ≥ 2 y dirección = largo"
    assert desde_json(a_json(conds)) == conds
    assert not solo_paper(conds) and solo_paper([Condicion(campo="confianza_claude", op=">=", valor=0.7)])


# ---------------------------------------------------------------- ajustes

def test_ajuste_dentro_de_rango_y_reversible(sesion, config):
    a = aplicar_ajuste(sesion, config, "multiplicador_volumen", 2.0, "prueba", 1)
    assert a.valor_anterior == 1.5
    assert config_efectiva(config, sesion).estrategia.multiplicador_volumen.valor == 2.0
    assert config.estrategia.multiplicador_volumen.valor == 1.5  # config.yaml no se toca
    revertir_ajuste(sesion, a.id, "prueba", 2)
    assert config_efectiva(config, sesion).estrategia.multiplicador_volumen.valor == 1.5
    assert sesion.query(HistorialAprendizaje).filter_by(entidad="ajuste").count() == 2


@pytest.mark.parametrize("parametro,valor", [
    ("multiplicador_volumen", 3.5),     # fuera del rango del dueño (1.2-3.0)
    ("ratio_tp", 1.0),                  # fuera del rango (1.5-3.0)
    ("sl_usd_por_operacion", 20),       # regla de riesgo: nunca ajustable
    ("ema_rapida", 21),                 # dentro de su rango, pero igual a la lenta (21): inválido
])
def test_ajustes_prohibidos(sesion, config, parametro, valor):
    with pytest.raises(AjusteInvalido):
        aplicar_ajuste(sesion, config, parametro, valor, "x", 1)
    assert sesion.query(AjusteParametro).count() == 0


# ---------------------------------------------------------------- motor (con efecto plantado)

def operaciones_falsas(config, desde, hasta):
    """Una operación cada 6 h. Volumen >= 2x rinde +0.8R y el resto -0.2R; con multiplicador_volumen=2.0 todo rinde
    +0.3R más. A partir de INVERSION ambos efectos se invierten (el mercado cambió)."""
    desde = max(desde, T0)
    fechas = pd.date_range(desde.ceil("6h"), hasta, freq="6h", inclusive="left")
    if len(fechas) == 0:
        return pd.DataFrame(columns=motor_mod.muestras_simuladas.__globals__["COLUMNAS"])
    rng = np.random.default_rng(int(desde.timestamp()) % 10_000)
    vol = np.where(np.arange(len(fechas)) % 2 == 0, 1.6, 2.4)
    invertido = fechas >= INVERSION
    efecto = np.where(vol >= 2, 0.8, -0.2)
    efecto = np.where(invertido, -efecto, efecto)
    if config.estrategia.multiplicador_volumen.valor == 2.0:
        efecto = efecto + np.where(invertido, -0.6, 0.3)
    return pd.DataFrame({
        "par": "BTC/USDT", "direccion": "largo", "ts_entrada": fechas, "hora_utc": fechas.hour, "dia_semana": fechas.dayofweek,
        "vol_rel": vol, "rsi": 50.0, "atr_pct": 1.0, "confianza_claude": np.nan,
        "r_multiple": efecto + rng.normal(0, 0.8, len(fechas)), "pnl_neto": 0.0,
    })


@pytest.fixture
def falso(monkeypatch):
    monkeypatch.setattr(MotorAprendizaje, "_senales", lambda self, config: {"__cfg__": config})
    monkeypatch.setattr(motor_mod, "muestras_simuladas",
                        lambda senales, pb, desde, hasta: operaciones_falsas(senales["__cfg__"], desde, hasta))


def motor(sesion, config, ahora):
    velas = {"BTC/USDT": pd.DataFrame(index=pd.date_range(T0, ahora, freq="D"))}
    return MotorAprendizaje(sesion, config, ahora, velas_por_par=velas)


def crear_filtro(m):
    return m.crear_hipotesis(origen="estadistico", tipo="filtro", efecto="mejor",
                             condiciones=[Condicion(campo="vol_rel", op=">=", valor=2.0)],
                             enunciado="Volumen >= 2x rinde mejor", explicacion="x")


def test_ciclo_de_vida_completo_de_una_leccion(sesion, config, falso):
    m = motor(sesion, config, T1)
    h = crear_filtro(m)
    assert crear_filtro(m) is None  # no se propone dos veces lo mismo
    inf = InformeSemanal()
    m.probar_hipotesis(h, inf)
    assert h.estado == "en_prueba" and json.loads(h.evidencia_backtest)["pasa"]
    m.probar_hipotesis(h, inf)
    assert h.estado == "en_prueba" and "Esperando datos nuevos" in h.motivo_estado  # aún no hay datos posteriores

    m2 = motor(sesion, config, T1 + pd.Timedelta(days=60))
    m2.probar_hipotesis(h, inf)
    assert h.estado == "validada"
    lec = sesion.query(Leccion).one()
    assert lec.estado == "vigente" and lec.codigo == "L0001"
    ev = json.loads(lec.evidencia)
    assert ev["adelante"]["n_grupo"] >= 30 and ev["adelante"]["r_medio_grupo"] > ev["adelante"]["r_medio_control"]
    vig = ProveedorLecciones(sesion).vigentes()
    assert vig[0]["id"] == "L0001" and "R medio" in vig[0]["enunciado"]

    # meses después el efecto se invierte: la lección se refuta y deja de pasarse a Claude
    m3 = motor(sesion, config, pd.Timestamp("2025-08-01", tz="UTC"))
    m3.revalidar(lec, inf)
    assert lec.estado == "refutada" and "invirtió" in lec.motivo_estado
    assert ProveedorLecciones(sesion).vigentes() == []
    eventos = [e.evento for e in sesion.query(HistorialAprendizaje).filter_by(entidad="hipotesis", entidad_id=h.id)]
    assert eventos == ["creada", "en_prueba", "validada"]


def test_hipotesis_falsa_se_descarta(sesion, config, falso):
    m = motor(sesion, config, T1)
    h = m.crear_hipotesis(origen="claude", tipo="filtro", efecto="mejor", enunciado="Volumen bajo rinde mejor",
                          explicacion="x", condiciones=[Condicion(campo="vol_rel", op="<", valor=2.0)])
    m.probar_hipotesis(h, InformeSemanal())
    assert h.estado == "descartada" and "No se confirmó" in h.motivo_estado


def test_hipotesis_de_parametro_aplica_ajuste_y_se_revierte_al_refutarse(sesion, config, falso):
    m = motor(sesion, config, T1)
    h = m.crear_hipotesis(origen="estadistico", tipo="parametro", efecto="mejor", parametro="multiplicador_volumen",
                          valor=2.0, enunciado="Multiplicador 2.0 mejora", explicacion="x")
    inf = InformeSemanal()
    m.probar_hipotesis(h, inf)
    assert h.estado == "en_prueba"
    m2 = motor(sesion, config, T1 + pd.Timedelta(days=60))
    m2.probar_hipotesis(h, inf)
    assert h.estado == "validada"
    assert config_efectiva(config, sesion).estrategia.multiplicador_volumen.valor == 2.0
    lec = sesion.query(Leccion).one()
    aj = sesion.get(AjusteParametro, lec.ajuste_id)
    assert aj.valor_anterior == 1.5 and "L0001" in aj.justificacion
    assert any("1.5 → 2" in a for a in inf.ajustes)

    m3 = motor(sesion, config, pd.Timestamp("2025-08-01", tz="UTC"))
    m3.revalidar(lec, inf)
    assert lec.estado == "refutada"
    assert aj.revertido_ms is not None and "refutada" in aj.motivo_reversion
    assert config_efectiva(config, sesion).estrategia.multiplicador_volumen.valor == 1.5


def test_hipotesis_sobre_claude_va_directo_a_prueba_hacia_adelante(sesion, config, falso):
    m = motor(sesion, config, T1)
    h = m.crear_hipotesis(origen="claude", tipo="filtro", efecto="mejor", enunciado="Confianza alta rinde mejor",
                          explicacion="x", condiciones=[Condicion(campo="confianza_claude", op=">=", valor=0.75)])
    m.probar_hipotesis(h, InformeSemanal())
    assert h.estado == "en_prueba" and "paper trading" in h.motivo_estado


def test_propuestas_fuera_de_rango_se_rechazan(sesion, config, falso):
    m = motor(sesion, config, T1)
    assert m.crear_hipotesis(origen="claude", tipo="parametro", efecto="mejor", parametro="ratio_tp", valor=5.0,
                             enunciado="TP 5:1", explicacion="x") is None
    assert m.crear_hipotesis(origen="claude", tipo="filtro", efecto="mejor", enunciado="sin condiciones",
                             explicacion="x", condiciones=[]) is None
    assert sesion.query(HistorialAprendizaje).filter_by(evento="propuesta_rechazada").count() == 2


def test_proponer_estadisticas_encuentra_el_efecto_plantado(sesion, config, falso):
    m = motor(sesion, config, T1)
    creadas = m.proponer_estadisticas()
    enunciados = [h.enunciado for h in creadas]
    assert any("volumen relativo ≥ 2 rinden mejor" in e for e in enunciados)
    assert any("multiplicador_volumen de 1.5 a 2" in e for e in enunciados)
    assert all(h.estado == "propuesta" for h in creadas)


def test_revision_de_claude(sesion, config, falso):
    rev = RevisionSemanal(
        nuevas_hipotesis=[
            HipotesisPropuesta(enunciado="Cortos en horario asiático rinden peor", explicacion="Menos liquidez",
                               tipo="filtro", condiciones=[Condicion(campo="hora_utc", op="<", valor=8)],
                               parametro=None, valor_propuesto=None, efecto_esperado="peor"),
            HipotesisPropuesta(enunciado="TP 5:1", explicacion="x", tipo="parametro", condiciones=[],
                               parametro="ratio_tp", valor_propuesto=5.0, efecto_esperado="mejor"),
        ],
        comentarios=[ComentarioExistente(codigo="H0001", comentario="Poca muestra", recomendacion="vigilar")],
        resumen_para_humano="Esta semana el bot aprendió que...",
    )
    falso_cli = ClienteFalso(respuesta(rev))
    m = motor(sesion, config, T1)
    informe = m.ciclo_semanal(RevisorClaude(ClienteClaude(config.claude, sesion, cliente=falso_cli)))
    assert any("[Claude] Cortos en horario asiático" in n for n in informe.nuevas)
    assert not any("TP 5:1" in n for n in informe.nuevas)       # fuera de rango: rechazada
    assert informe.comentario_claude.startswith("Esta semana")
    assert "tramo de exploración" in falso_cli.llamadas[0]["messages"][0]["content"]
    assert sesion.query(HistorialAprendizaje).filter_by(evento="claude_vigilar").count() == 1
    assert "## Hipótesis nuevas" in informe.texto()


def test_ciclo_del_bot_usa_la_config_con_ajustes(sesion, config):
    from bot.ciclo import Ciclo
    from bot.notificaciones import Notificador
    aplicar_ajuste(sesion, config, "multiplicador_volumen", 2.0, "prueba", 1)
    c = Ciclo(sesion, config, mercado=None, carteras={}, cliente_claude=None, avisos=Notificador())
    c._refrescar_config()
    assert c.config.estrategia.multiplicador_volumen.valor == 2.0
    assert c._parametros().multiplicador_volumen == 2.0
