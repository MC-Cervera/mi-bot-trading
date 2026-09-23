import json

import pandas as pd
import pytest

from bot.config import cargar_config
from bot.db.modelos import LlamadaClaude, Noticia, Vela
from bot.ia.cliente import ClienteClaude
from bot.noticias.analisis import AnalisisNoticia, LoteAnalisis, analizar_pendientes, guardar_nuevas, noticias_recientes
from bot.noticias.filtro import detectar_activos
from bot.noticias.fuentes import NoticiaCruda, parsear_rss
from bot.noticias.impacto import estadisticas_impacto, historial_para, medir_impactos
from tests.claude_falso import ClienteFalso, respuesta

H = 3_600_000
T0 = 1_720_000_800_000 - (1_720_000_800_000 % H)  # alineado a la hora
BASES = ["BTC", "ETH", "SOL", "LINK", "DOT", "UNI"]

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>X</title>
<item><title>Bitcoin ETF &amp; the &lt;b&gt;SEC&lt;/b&gt;</title><link>https://ej.com/a</link>
<description>&lt;p&gt;The SEC approved a spot bitcoin ETF.&lt;/p&gt;</description>
<pubDate>Mon, 01 Jul 2024 12:00:00 GMT</pubDate></item>
<item><title>Futuro</title><link>https://ej.com/b</link><pubDate>Mon, 01 Jul 2030 12:00:00 GMT</pubDate></item>
<item><title>Sin enlace</title></item>
</channel></rss>"""


def test_parsear_rss():
    ahora = 1_750_000_000_000
    ns = parsear_rss(RSS, "ej.com", ahora)
    assert len(ns) == 2
    assert ns[0].titulo == "Bitcoin ETF & the SEC"
    assert "approved a spot bitcoin ETF" in ns[0].resumen and "<p>" not in ns[0].resumen
    assert ns[0].publicada_ms == 1_719_835_200_000
    assert ns[1].publicada_ms == ahora  # fechas futuras no se aceptan


@pytest.mark.parametrize("texto,esperado", [
    ("Bitcoin ETF approved by the SEC", ["BTC", "MERCADO"]),
    ("Click this link to download the app", []),
    ("Chainlink (LINK) rallies 10%", ["LINK"]),
    ("$SOL pumps after upgrade", ["SOL"]),
    ("Polkadot parachain news", ["DOT"]),
    ("the dot com era and uni students", []),
    ("Uniswap v5 launches; UNI jumps", ["UNI"]),
    ("Ether and Solana lead gains", ["ETH", "SOL"]),
])
def test_detectar_activos(texto, esperado):
    assert detectar_activos(texto, BASES) == esperado


def cruda(url, titulo, publicada=T0):
    return NoticiaCruda(fuente="ej.com", titulo=titulo, resumen="", url=url, publicada_ms=publicada)


def test_guardar_nuevas_evita_duplicados_y_descarta_irrelevantes(sesion):
    crudas = [cruda("https://x/1", "Bitcoin sube"), cruda("https://x/2", "Receta de pastel"), cruda("https://x/1", "Bitcoin sube")]
    nuevas = guardar_nuevas(sesion, crudas, BASES, T0)
    assert len(nuevas) == 2
    assert guardar_nuevas(sesion, crudas, BASES, T0) == []
    pastel = sesion.query(Noticia).filter_by(url="https://x/2").one()
    assert pastel.analizada and pastel.relevante is False  # no se gasta API en ella


def analisis(id_, **kw):
    base = dict(id=id_, relevante=True, resumen="Resumen", activos=["BTC"], sentimiento=0.5, impacto="medio",
                horizonte="horas", tema="regulacion")
    return AnalisisNoticia(**(base | kw))


def test_analizar_pendientes_con_claude(sesion, config):
    guardar_nuevas(sesion, [cruda("https://x/1", "Bitcoin ETF approved"), cruda("https://x/2", "Ethereum upgrade"),
                            cruda("https://x/3", "Solana outage")], BASES, T0)
    ids = [n.id for n in sesion.query(Noticia).order_by(Noticia.id)]
    falso = ClienteFalso(respuesta(LoteAnalisis(noticias=[
        analisis(ids[0], impacto="alto", activos=["BTC", "DOGE"]),   # DOGE no se opera: se descarta
        analisis(ids[1], relevante=False, impacto="bajo"),
    ])))
    cliente = ClienteClaude(config.claude, sesion, cliente=falso)
    altas = analizar_pendientes(sesion, cliente, BASES, T0 + 1000, 15, "low")
    assert [n.id for n in altas] == [ids[0]]
    n1, n2, n3 = (sesion.get(Noticia, i) for i in ids)
    assert json.loads(n1.activos) == ["BTC"]
    assert n2.relevante is False
    assert n3.analizada and n3.relevante is None  # omitida por Claude: no se vuelve a pagar
    # el contenido externo va como datos en el mensaje de usuario, el sistema lleva cache_control
    llamada = falso.llamadas[0]
    assert "Bitcoin ETF approved" in llamada["messages"][0]["content"]
    assert llamada["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert llamada["output_format"] is LoteAnalisis
    assert sesion.query(LlamadaClaude).one().costo_usd == pytest.approx(1000 * 2e-6 + 500 * 10e-6)


def test_presupuesto_agotado_no_llama_a_claude(sesion, config):
    from bot.ia.cliente import ahora_ms
    sesion.add(LlamadaClaude(ts_ms=ahora_ms(), proposito="decision", modelo="x", estado="ok", contexto="", costo_usd=14.95))
    sesion.commit()
    guardar_nuevas(sesion, [cruda("https://x/1", "Bitcoin news")], BASES, T0)
    falso = ClienteFalso()
    cliente = ClienteClaude(config.claude, sesion, cliente=falso)
    assert analizar_pendientes(sesion, cliente, BASES, T0 + 1000, 15, "low") == []
    assert falso.llamadas == []
    assert sesion.query(LlamadaClaude).filter_by(estado="presupuesto_agotado").count() == 1
    assert sesion.query(Noticia).one().analizada is False  # queda pendiente para cuando haya presupuesto


def test_noticias_recientes_no_mira_al_futuro(sesion):
    sesion.add_all([
        Noticia(huella="a", fuente="f", titulo="vieja", url="u1", publicada_ms=T0, obtenida_ms=T0, relevante=True,
                analizada=True, activos='["BTC"]'),
        Noticia(huella="b", fuente="f", titulo="futura", url="u2", publicada_ms=T0, obtenida_ms=T0 + 5 * H,
                relevante=True, analizada=True, activos='["BTC"]'),
        Noticia(huella="c", fuente="f", titulo="otra moneda", url="u3", publicada_ms=T0, obtenida_ms=T0,
                relevante=True, analizada=True, activos='["ETH"]'),
    ])
    sesion.commit()
    titulos = [n.titulo for n in noticias_recientes(sesion, "BTC", T0 - H, T0 + H)]
    assert titulos == ["vieja"]


def velas_lineales(sesion, par, precio0, paso_pct):
    for i in range(-2, 30):
        o = precio0 * (1 + paso_pct / 100) ** i
        c = precio0 * (1 + paso_pct / 100) ** (i + 1)
        sesion.add(Vela(exchange="binance", par=par, temporalidad="1h", ts=T0 + i * H, open=o, high=max(o, c),
                        low=min(o, c), close=c, volume=1))
    sesion.commit()


def test_medir_impacto_y_estadisticas(sesion):
    velas_lineales(sesion, "BTC/USDT", 100, 1.0)   # sube 1% por hora
    velas_lineales(sesion, "ETH/USDT", 50, 0.0)    # plano
    for k in range(6):
        sesion.add(Noticia(huella=f"n{k}", fuente="f", titulo=f"BTC {k}", url=f"u{k}", publicada_ms=T0 - 10 * 60_000,
                           obtenida_ms=T0, relevante=True, analizada=True, activos='["BTC"]', sentimiento=0.8,
                           impacto="alto", tema="etf_institucional"))
    sesion.commit()
    assert medir_impactos(sesion, "binance", ["BTC/USDT", "ETH/USDT"], T0 + 48 * H) == 6
    stats = estadisticas_impacto(sesion)
    fila = stats.iloc[0]
    assert fila["muestras"] == 6
    assert fila["mov_abs_medio_1h_pct"] == pytest.approx(1.0)
    assert fila["mov_abs_medio_4h_pct"] == pytest.approx((1.01 ** 4 - 1) * 100)
    # mercado = media(BTC, ETH): lo propio de BTC es la mitad de su subida
    assert fila["anormal_abs_medio_4h_pct"] == pytest.approx((1.01 ** 4 - 1) * 100 / 2)
    assert fila["acierto_direccion_4h_pct"] == 100
    texto = historial_para(stats, "etf_institucional", "alto", 5).texto
    assert "6 noticias previas" in texto and "100%" in texto
    assert "Sin historial suficiente" in historial_para(stats, "etf_institucional", "alto", 10).texto
    assert "Sin historial suficiente (0" in historial_para(stats, "hackeo_seguridad", "alto", 5).texto


def test_impacto_incompleto_se_completa_despues(sesion):
    velas_lineales(sesion, "BTC/USDT", 100, 1.0)
    sesion.add(Noticia(huella="n", fuente="f", titulo="BTC", url="u", publicada_ms=T0, obtenida_ms=T0, relevante=True,
                       analizada=True, activos='["BTC"]', sentimiento=0.5, impacto="medio", tema="otro"))
    sesion.commit()
    from bot.db.modelos import ImpactoNoticia
    # solo existen 30 velas: con "ahora" a las 5 h aún no hay dato de 24 h
    sesion.query(Vela).filter(Vela.ts > T0 + 5 * H).delete()
    sesion.commit()
    medir_impactos(sesion, "binance", ["BTC/USDT"], T0 + 6 * H)
    imp = sesion.query(ImpactoNoticia).one()
    assert imp.ret_4h is not None and imp.ret_24h is None and not imp.completo
