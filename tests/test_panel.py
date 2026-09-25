"""Pruebas del panel: consultas de datos y recorrido de todas las secciones sin errores."""
import json

import pandas as pd
import pytest

from bot.cartera import CARTERA_CLAUDE, CARTERA_SOLO
from bot.db import crear_motor, crear_sesion
from bot.db.modelos import (
    EstadoCartera, EventoRiesgo, Hipotesis, ImpactoNoticia, LlamadaClaude, Noticia, Operacion, PuntoCapital, Vela,
)
from panel import datos, graficos

AHORA = pd.Timestamp.now(tz="UTC").floor("h")


def ms(t):
    return int(t.timestamp() * 1000)


def poblar(s):
    for c in (CARTERA_CLAUDE, CARTERA_SOLO):
        s.add(EstadoCartera(cartera=c, capital_inicial=1000, pico=1010, estado="activo", motivo=""))
    s.add(Vela(exchange="binance", par="BTC/USDT", temporalidad="1h", ts=ms(AHORA - pd.Timedelta(hours=1)),
               open=100, high=101, low=99, close=102, volume=1))
    s.add(LlamadaClaude(ts_ms=ms(AHORA - pd.Timedelta(days=1)), proposito="decision", modelo="claude-sonnet-5",
                        estado="ok", contexto="ctx", costo_usd=0.5, respuesta=json.dumps({"accion": "comprar"})))
    s.flush()
    ops = [  # (cartera, pnl, dias atrás, estado)
        (CARTERA_CLAUDE, 12.0, 3, "cerrada"), (CARTERA_CLAUDE, -8.0, 2, "cerrada"), (CARTERA_SOLO, -8.0, 3, "cerrada"),
        (CARTERA_SOLO, 4.0, 2, "cerrada"), (CARTERA_CLAUDE, None, 0, "abierta"),
    ]
    for i, (c, pnl, d, est) in enumerate(ops):
        t = AHORA - pd.Timedelta(days=d, hours=3)
        s.add(Operacion(
            id_cliente=f"x{i}", cartera=c, par="BTC/USDT", direccion="largo", estado=est, ts_senal_ms=ms(t),
            ts_entrada_ms=ms(t), ts_salida_ms=ms(t + pd.Timedelta(hours=2)) if pnl is not None else None,
            precio_entrada=100, precio_salida=101 if pnl is not None else None, cantidad=2, nocional=200, stop_loss=96,
            take_profit=108, riesgo_usd=8, comisiones=0.2, funding=0, pnl_neto=pnl,
            r_multiple=pnl / 8 if pnl is not None else None, motivo_salida="take_profit" if pnl and pnl > 0 else "stop_loss",
            sugerida_por="senal_tecnica+claude" if c == CARTERA_CLAUDE else "senal_tecnica",
            confianza_claude=0.7 if c == CARTERA_CLAUDE else None, llamada_claude_id=1 if c == CARTERA_CLAUDE else None,
            reglas_que_permitieron='["R1: bot activo"]', diario_entrada="### Entrada", diario_salida="### Salida",
            analisis_post="### Análisis"))
    for h in range(48):
        for c, base in ((CARTERA_CLAUDE, 1000), (CARTERA_SOLO, 1000)):
            s.add(PuntoCapital(cartera=c, ts_ms=ms(AHORA - pd.Timedelta(hours=48 - h)), capital=base + (h % 7) - 3,
                               realizado=0, posiciones_abiertas=0))
    s.add(EventoRiesgo(ts_ms=ms(AHORA), cartera=CARTERA_CLAUDE, tipo="bloqueo", par="ETH/USDT", detalle="R5: máximo"))
    s.add(Noticia(huella="n", fuente="f", titulo="ETF aprobado", url="https://x", publicada_ms=ms(AHORA - pd.Timedelta(hours=5)),
                  obtenida_ms=ms(AHORA - pd.Timedelta(hours=5)), analizada=True, relevante=True, activos='["BTC"]',
                  sentimiento=0.6, impacto="alto", horizonte="horas", tema="etf_institucional", resumen="r"))
    s.flush()
    s.add(ImpactoNoticia(noticia_id=1, par="BTC/USDT", precio_inicial=100, ret_1h=0.5, ret_4h=1.2, ret_24h=2.0,
                         anormal_4h=0.8, anormal_24h=1.0, completo=True))
    s.add(Hipotesis(codigo="H0001", enunciado="Volumen alto rinde mejor", explicacion="e", origen="estadistico",
                    tipo="filtro", condiciones='[{"campo": "vol_rel", "op": ">=", "valor": 2.0}]', efecto_esperado="mejor",
                    estado="descartada", motivo_estado="No pasó", creada_ms=ms(AHORA), actualizada_ms=ms(AHORA), firma="f",
                    evidencia_backtest=json.dumps({"explicacion": "NO PASA: casualidad"})))
    s.commit()


@pytest.fixture(scope="module")
def ruta_db(tmp_path_factory):
    ruta = tmp_path_factory.mktemp("panel") / "panel.db"
    s = crear_sesion(crear_motor(ruta))
    poblar(s)
    s.close()
    return ruta


@pytest.fixture
def s(ruta_db):
    return crear_sesion(crear_motor(ruta_db))


def test_resumen_carteras(s):
    r = {x["cartera"]: x for x in datos.resumen_carteras(s)}
    assert r[CARTERA_CLAUDE]["posiciones_abiertas"] == 1
    assert r[CARTERA_CLAUDE]["pnl_total"] == pytest.approx(12 - 8 + (102 - 100) * 2)  # abierta valorada a 102
    assert r[CARTERA_SOLO]["capital"] == pytest.approx(996)


def test_posiciones_abiertas(s):
    pos = datos.posiciones_abiertas(s, ahora=AHORA)
    assert len(pos) == 1
    p = pos.iloc[0]
    assert p["precio_actual"] == 102 and p["resultado_usd"] == pytest.approx(4)  # (102 - 100) x 2
    assert p["resultado_r"] == pytest.approx(0.5) and p["horas_abierta"] == pytest.approx(3)
    assert p["dist_sl_pct"] == pytest.approx(6 / 102 * 100) and p["dist_tp_pct"] == pytest.approx(6 / 102 * 100)


def test_resultados_por_periodo_y_metricas(s):
    ops = datos.operaciones(s)
    for p in "DWMY":
        t = datos.resultados_por_periodo(ops, p)
        assert t.groupby("cartera")["resultado_usd"].sum().to_dict() == {CARTERA_CLAUDE: 4.0, CARTERA_SOLO: -4.0}
    m = datos.metricas(s, CARTERA_CLAUDE)
    assert m["tasa_acierto_pct"] == 50 and m["factor_beneficio"] == pytest.approx(1.5)


def test_curva_y_drawdown(s):
    c = datos.curva_capital(s)
    assert set(c["cartera"]) == {CARTERA_CLAUDE, CARTERA_SOLO}
    assert (c["drawdown_pct"] <= 0).all()


def test_valor_de_claude(s):
    v = datos.valor_de_claude(s)
    assert v["aporte_bruto"] == pytest.approx(8.0) and v["aporte_neto"] == pytest.approx(7.5)
    serie = datos.serie_valor_claude(s)
    assert serie["neto_acumulado"].iloc[-1] == pytest.approx(7.5)


def test_detalle_y_aprendizaje(s):
    op_id = datos.operaciones(s, CARTERA_CLAUDE).iloc[-1]["id"]
    d = datos.detalle_operacion(s, int(op_id))
    assert d["decision"] == {"accion": "comprar"} and d["reglas"] == ["R1: bot activo"]
    assert [h.codigo for h in datos.hipotesis(s, "volumen")] == ["H0001"]
    assert datos.hipotesis(s, "no existe") == []
    assert datos.hipotesis(s, estados=["validada"]) == []
    assert datos.evidencia_legible(datos.hipotesis(s)[0].evidencia_backtest) == "NO PASA: casualidad"


@pytest.mark.parametrize("modo", ["light", "dark"])
def test_graficos_usan_color_fijo_por_cartera(s, modo):
    fig = graficos.curva_capital(datos.curva_capital(s), modo)
    colores = {t.name: t.line.color for t in fig.data}
    assert colores["Técnico + Claude"] == graficos.color_cartera(CARTERA_CLAUDE, modo)
    assert colores["Técnico solo"] == graficos.color_cartera(CARTERA_SOLO, modo)
    assert len(fig.layout.annotations) == 2  # etiqueta directa por serie
    # una sola escala vertical: ninguna traza usa un segundo eje y
    assert all(getattr(t, "yaxis", None) in (None, "y") for t in fig.data)


def test_todas_las_secciones_se_muestran_sin_errores(ruta_db, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("PANEL_DB", str(ruta_db))
    monkeypatch.setenv("PANEL_CLAVE", "")
    at = AppTest.from_file("../panel/app.py", default_timeout=60).run()
    assert not at.exception
    for seccion in ["Resumen", "Operaciones", "Detalle de operación", "Aprendizaje", "Noticias", "Costos"]:
        at.sidebar.radio[0].set_value(seccion).run()
        assert not at.exception, (seccion, at.exception)
        assert any(seccion.split(" ")[0] in h.value for h in at.header), seccion


def test_panel_pide_clave_si_esta_configurada(ruta_db, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("PANEL_DB", str(ruta_db))
    monkeypatch.setenv("PANEL_CLAVE", "secreta")
    at = AppTest.from_file("../panel/app.py", default_timeout=60).run()
    assert at.text_input[0].label == "Clave del panel" and not at.header
    at.text_input[0].input("mala").run()
    assert at.error and at.error[0].value == "Clave incorrecta"
    at.text_input[0].input("secreta").run()
    assert any("Resumen" in h.value for h in at.header)


def test_proceso_latido_y_parada(tmp_path):
    import time

    from bot import proceso
    lat, parar = tmp_path / "latido.json", tmp_path / "PARAR"
    assert not proceso.estado(ruta_latido=lat, ruta_parar=parar).en_marcha
    proceso.escribir_latido(pid=123, iniciado=1000.0, ruta=lat)
    e = proceso.estado(ruta_latido=lat, ruta_parar=parar)
    assert e.en_marcha and e.pid == 123 and e.iniciado == 1000.0
    assert not proceso.estado(ahora=time.time() + 600, ruta_latido=lat, ruta_parar=parar).en_marcha  # latido viejo
    proceso.pedir_parada(parar)
    assert proceso.estado(ruta_latido=lat, ruta_parar=parar).parada_solicitada
    proceso.limpiar_parada(parar)
    assert not proceso.parada_solicitada(parar)


def test_iniciar_no_duplica_el_bot(tmp_path, monkeypatch):
    from bot import proceso
    monkeypatch.setattr(proceso, "ARCHIVO_LATIDO", tmp_path / "latido.json")
    monkeypatch.setattr(proceso, "ARCHIVO_PARAR", tmp_path / "PARAR")
    monkeypatch.setattr(proceso, "ARCHIVO_CONSOLA", tmp_path / "consola.log")
    monkeypatch.setattr(proceso.estado, "__defaults__", (None, tmp_path / "latido.json", tmp_path / "PARAR"))
    lanzados = []

    class Falso:
        def __init__(self, args, **kw):
            lanzados.append((args, kw))
            self.pid = 4321

    assert proceso.iniciar(popen=Falso) == 4321
    args, kw = lanzados[0]
    assert args[-1].endswith("bot.py") and kw["stdin"] is not None
    proceso.escribir_latido(pid=4321, ruta=tmp_path / "latido.json")
    with pytest.raises(RuntimeError, match="ya está en marcha"):
        proceso.iniciar(popen=Falso)


def test_en_windows_el_bot_arranca_sin_ventana():
    from bot import proceso
    assert proceso.FLAGS_WINDOWS & proceso.CREATE_NO_WINDOW
    assert not proceso.FLAGS_WINDOWS & 0x00000008  # DETACHED_PROCESS abría una ventana vacía con el .venv


def test_panel_muestra_boton_de_iniciar(ruta_db, monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest

    from bot import proceso
    monkeypatch.setenv("PANEL_DB", str(ruta_db))
    monkeypatch.setenv("PANEL_CLAVE", "")
    monkeypatch.setattr(proceso, "ARCHIVO_LATIDO", tmp_path / "latido.json")
    monkeypatch.setattr(proceso.estado, "__defaults__", (None, tmp_path / "latido.json", tmp_path / "PARAR"))
    at = AppTest.from_file("../panel/app.py", default_timeout=60).run()
    assert not at.exception
    assert any("Iniciar bot" in b.label for b in at.button)
    proceso.escribir_latido(pid=1, ruta=tmp_path / "latido.json")
    at.run()
    assert any("Detener bot" in b.label for b in at.button)
    assert any("Bot en marcha" in x.value for x in at.success)
