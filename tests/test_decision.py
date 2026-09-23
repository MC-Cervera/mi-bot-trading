import pandas as pd
import pytest
from pydantic import ValidationError

from bot.db.modelos import LlamadaClaude, Noticia
from bot.ia.cliente import ClienteClaude, ahora_ms, calcular_costo
from bot.ia.contexto import contexto_para_senal
from bot.ia.decision import (
    ContextoDecision, DecisionClaude, debe_consultar_por_noticia, decidir, problemas_de_coherencia,
)
from bot.senales import SenalCandidata
from tests.claude_falso import ClienteFalso, respuesta

AHORA = pd.Timestamp("2024-07-01 13:00", tz="UTC")


def senal(direccion="largo", precio=100.0):
    d = 1 if direccion == "largo" else -1
    return SenalCandidata(
        par="BTC/USDT", temporalidad="1h", ts_vela=AHORA - pd.Timedelta(hours=1), direccion=direccion,
        precio_referencia=precio, stop_loss=precio - d * 2, take_profit=precio + d * 4, ema_rapida=99.5,
        ema_lenta=99.0, vol_rel=2.1, rsi=55.0, atr=1.3,
        parametros={"multiplicador_volumen": 1.5, "atr_mult_sl": 1.5, "ratio_tp": 2.0},
    )


def ctx(s=None, posiciones=None, lecciones=None, disparador="senal"):
    return ContextoDecision(disparador=disparador, par="BTC/USDT", ahora=AHORA, precio_actual=100.0, senal=s,
                            posiciones_abiertas=posiciones or [], lecciones=lecciones or [], capital_usd=1000,
                            libre_usd=1000)


def decision(**kw):
    base = dict(accion="comprar", par="BTC/USDT", confianza=0.7, tamano_sugerido_pct=100, stop_loss=98.0,
                take_profit=104.0, razonamiento="ok", hipotesis_que_aplica=[], factores_a_favor=["volumen"],
                factores_en_contra=[])
    return DecisionClaude(**(base | kw))


def test_esquema_valida_rangos():
    with pytest.raises(ValidationError):
        decision(confianza=1.5)
    with pytest.raises(ValidationError):
        decision(tamano_sugerido_pct=150)
    with pytest.raises(ValidationError):
        decision(accion="apostar")


def test_decision_coherente_no_tiene_problemas():
    assert problemas_de_coherencia(decision(), ctx(senal())) == []
    assert problemas_de_coherencia(decision(accion="mantener", stop_loss=0, take_profit=0), ctx(senal())) == []


@pytest.mark.parametrize("d,c,texto", [
    (dict(accion="vender", stop_loss=102, take_profit=96), dict(s=senal("largo")), "contra una señal"),
    (dict(), dict(s=None, disparador="noticia_alto_impacto"), "sin señal técnica"),
    (dict(stop_loss=101), dict(s=senal()), "lado equivocado"),
    (dict(take_profit=99), dict(s=senal()), "lado equivocado"),
    (dict(accion="cerrar"), dict(s=None), "no existe"),
    (dict(hipotesis_que_aplica=["H99"]), dict(s=senal(), lecciones=[{"id": "L1", "enunciado": "x"}]), "inexistentes"),
    (dict(par="ETH/USDT"), dict(s=senal()), "consulta era sobre"),
])
def test_incoherencias_se_bloquean(d, c, texto):
    problemas = problemas_de_coherencia(decision(**d), ctx(**c))
    assert any(texto in p for p in problemas), problemas


def test_corto_coherente():
    assert problemas_de_coherencia(decision(accion="vender", stop_loss=102, take_profit=96), ctx(senal("corto"))) == []


def test_decidir_ok_registra_costo(sesion, config):
    falso = ClienteFalso(respuesta(decision(), entrada=3000, salida=800, cache_lectura=1500))
    r = decidir(ClienteClaude(config.claude, sesion, cliente=falso), ctx(senal()), "medium", senal_id=7)
    assert r.aprobada_por_claude
    kw = falso.llamadas[0]
    assert kw["model"] == "claude-sonnet-5"
    assert kw["thinking"] == {"type": "adaptive"} and kw["output_config"] == {"effort": "medium"}
    assert kw["output_format"] is DecisionClaude
    ll = sesion.query(LlamadaClaude).one()
    assert ll.estado == "ok" and ll.senal_id == 7 and ll.par == "BTC/USDT"
    assert ll.costo_usd == pytest.approx((3000 * 2 + 800 * 10 + 1500 * 0.2) / 1e6)
    assert "Señal técnica" in ll.contexto


@pytest.mark.parametrize("resp,estado", [
    (respuesta(None, stop_reason="refusal"), "rechazo"),
    (respuesta(None, stop_reason="max_tokens"), "invalida"),
    (RuntimeError("se cayó la red"), "error"),
])
def test_sin_respuesta_valida_no_se_opera(sesion, config, resp, estado):
    r = decidir(ClienteClaude(config.claude, sesion, cliente=ClienteFalso(resp)), ctx(senal()), "medium")
    assert r.decision is None and not r.aprobada_por_claude
    assert "no se opera" in r.problemas[0]
    assert r.llamada.estado == estado


def test_rechazo_de_claude_mantener_no_abre(sesion, config):
    falso = ClienteFalso(respuesta(decision(accion="mantener", confianza=0.3, stop_loss=0, take_profit=0)))
    r = decidir(ClienteClaude(config.claude, sesion, cliente=falso), ctx(senal()), "medium")
    assert r.decision is not None and not r.aprobada_por_claude


def test_presupuesto_agotado_bloquea_decision(sesion, config):
    sesion.add(LlamadaClaude(ts_ms=ahora_ms(), proposito="noticias", modelo="x", estado="ok", contexto="", costo_usd=15))
    sesion.commit()
    falso = ClienteFalso()
    r = decidir(ClienteClaude(config.claude, sesion, cliente=falso), ctx(senal()), "medium")
    assert falso.llamadas == [] and r.decision is None
    assert "presupuesto_agotado" in r.problemas[0]


def test_costo_por_millon():
    from types import SimpleNamespace
    from bot.config import PreciosClaude
    u = SimpleNamespace(input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    assert calcular_costo(u, PreciosClaude(entrada=2, salida=10, cache_lectura=0.2, cache_escritura=2.5)) == 2


def test_contexto_incluye_todo_y_no_noticias_futuras(sesion, config):
    t = int(AHORA.timestamp() * 1000)
    sesion.add_all([
        Noticia(huella="a", fuente="f", titulo="ETF aprobado", url="u1", publicada_ms=t - 3_600_000,
                obtenida_ms=t - 3_000_000, relevante=True, analizada=True, activos='["BTC"]', sentimiento=0.7,
                impacto="alto", tema="etf_institucional", resumen="Aprobado"),
        Noticia(huella="b", fuente="f", titulo="NOTICIA DEL FUTURO", url="u2", publicada_ms=t + 3_600_000,
                obtenida_ms=t + 3_600_000, relevante=True, analizada=True, activos='["BTC"]', sentimiento=-1,
                impacto="alto", tema="hackeo_seguridad"),
    ])
    sesion.commit()
    idx = pd.date_range(AHORA - pd.Timedelta(hours=30), periods=30, freq="h")
    df = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 5.0, "ema_rapida": 99.5,
                       "ema_lenta": 99.0, "rsi": 55.0, "vol_rel": 1.0}, index=idx)
    c = contexto_para_senal(sesion, config, senal(), df, AHORA, [{"par": "ETH/USDT", "direccion": "largo"}], 1000, 900,
                            lecciones=[{"id": "L1", "enunciado": "Volumen >2x funciona mejor en BTC"}])
    texto = c.a_texto()
    for trozo in ("Señal técnica", "cruzó por encima", "ETF aprobado", "Sin historial suficiente", "ETH/USDT",
                  "Capital: 1000.00", "perdida_maxima_por_operacion_usd", "[L1] Volumen >2x"):
        assert trozo in texto, trozo
    assert "NOTICIA DEL FUTURO" not in texto
    assert len(c.velas_recientes) == 24 and c.velas_recientes.index[-1] <= senal().ts_vela


def test_consulta_por_noticia_solo_con_posicion_afectada():
    n = Noticia(relevante=True, impacto="alto", activos='["ETH"]')
    pos = [{"par": "ETH/USDT"}, {"par": "BTC/USDT"}]
    assert debe_consultar_por_noticia(n, pos) == ["ETH/USDT"]
    assert debe_consultar_por_noticia(Noticia(relevante=True, impacto="medio", activos='["ETH"]'), pos) == []
    assert debe_consultar_por_noticia(Noticia(relevante=True, impacto="alto", activos='["MERCADO"]'), pos) == ["ETH/USDT", "BTC/USDT"]
    assert debe_consultar_por_noticia(n, []) == []
