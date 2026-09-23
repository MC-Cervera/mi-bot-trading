import sqlite3

import pandas as pd

from bot.checklist import FALLA, MANUAL, OK, dias_sin_interrupcion, evaluar
from bot.config import Secretos
from bot.db.modelos import PuntoCapital
from bot.informe_final import generar
from bot.respaldo import respaldar
from tests.test_panel import poblar


def puntos_capital(s, horas, hueco_en=None):
    t0 = pd.Timestamp("2026-01-01", tz="UTC")
    for h in range(horas):
        if hueco_en and hueco_en[0] <= h < hueco_en[1]:
            continue
        s.add(PuntoCapital(cartera="tecnico_solo", ts_ms=int((t0 + pd.Timedelta(hours=h)).timestamp() * 1000),
                           capital=1000, realizado=0, posiciones_abiertas=0))
    s.commit()


def test_dias_sin_interrupcion(sesion):
    puntos_capital(sesion, 24 * 10, hueco_en=(24 * 3, 24 * 3 + 5))  # hueco de 5 h el día 3
    assert 6.5 < dias_sin_interrupcion(sesion) < 7.0


def test_informe_sin_datos(sesion, config):
    assert "Aún no hay datos" in generar(sesion, config)


def test_informe_dice_si_claude_no_mejora(sesion, config):
    poblar(sesion)  # Claude: +4 USD, gasto 0.5; solo: -4 USD
    txt = generar(sesion, config)
    assert "Muestra pequeña" in txt and "14" in txt
    assert "descontando su costo (0.50 USD), se obtuvo +3.50 USD" in txt
    assert "NO está demostrado" in txt


def test_checklist_bloquea_el_paso_a_real(sesion, config):
    poblar(sesion)
    puntos = {p.nombre: p for p in evaluar(sesion, config, Secretos(), correr_pruebas=False)}
    assert puntos["Al menos 14 días de paper trading"].estado == FALLA
    assert puntos["Reglas de riesgo coherentes con el capital real (50 USD)"].estado == FALLA
    assert puntos["Ejecución real implementada y revisada"].estado == FALLA
    assert any(p.estado == MANUAL for p in puntos.values())


def test_checklist_capital_real_suficiente(sesion, config):
    cfg = config.model_copy(deep=True)
    cfg.capital.real_usd = 1000
    p = {x.nombre: x for x in evaluar(sesion, cfg, Secretos(), correr_pruebas=False)}
    assert p["Reglas de riesgo coherentes con el capital real (1000 USD)"].estado == OK


def test_respaldo(tmp_path):
    db = tmp_path / "bot.db"
    with sqlite3.connect(db) as c:
        c.execute("create table t(x)")
        c.execute("insert into t values (1)")
    for d in range(16):
        respaldar(db, ahora=pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(days=d))
    copias = sorted((tmp_path / "respaldos").glob("bot_*.db"))
    assert len(copias) == 14 and copias[-1].name == "bot_20260116_0000.db"
    with sqlite3.connect(copias[-1]) as c:
        assert c.execute("select x from t").fetchone() == (1,)
