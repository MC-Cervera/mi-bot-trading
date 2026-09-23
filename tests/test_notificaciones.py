import urllib.request

from bot.ejecucion.binance_demo import id_binance
from bot.notificaciones import Notificador


def test_sin_configurar_no_envia_nada(monkeypatch):
    def prohibido(*a, **k):
        raise AssertionError("no debe llamar a la red")
    monkeypatch.setattr(urllib.request, "urlopen", prohibido)
    n = Notificador(token="", chat_id="", habilitado=True)
    assert n.enviar("hola") is False and n.enviados == ["hola"]


def test_fallo_de_red_no_detiene_el_bot(monkeypatch):
    def falla(*a, **k):
        raise OSError("sin red")
    monkeypatch.setattr(urllib.request, "urlopen", falla)
    assert Notificador("t", "c", True).enviar("hola") is False


def test_id_de_orden_valido_para_binance():
    cid = id_binance("tecnico_claude-BTCUSDT-1719835200000", "-sl")
    assert len(cid) <= 36 and cid.endswith("-sl")
    assert id_binance("a b/c?", "") == "a_b/c_"
