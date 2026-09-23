"""Genera una base de datos de DEMOSTRACIÓN (data/demo.db) para ver el panel sin esperar semanas.

⚠️ Todo es SIMULADO: precios sintéticos (paseo aleatorio) y un "Claude" falso que no llama a la API.
Los resultados NO dicen nada sobre la estrategia real. Tu base real (data/bot.db) no se toca.

Uso:
  python scripts/datos_demo.py
  set PANEL_DB=data\\demo.db   (PowerShell: $env:PANEL_DB="data/demo.db")
  streamlit run panel/app.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from bot.aprendizaje.motor import MotorAprendizaje  # noqa: E402
from bot.cartera import CARTERA_CLAUDE, CARTERA_SOLO, GestorCartera  # noqa: E402
from bot.ciclo import Ciclo  # noqa: E402
from bot.config import RAIZ, cargar_config  # noqa: E402
from bot.datos.historico import guardar_velas  # noqa: E402
from bot.db import crear_motor, crear_sesion  # noqa: E402
from bot.db.modelos import Noticia  # noqa: E402
from bot.ejecucion.simulado import BrokerSimulado  # noqa: E402
from bot.ia.cliente import ClienteClaude  # noqa: E402
from bot.ia.decision import DecisionClaude  # noqa: E402
from bot.noticias.analisis import LoteAnalisis  # noqa: E402
from bot.noticias.impacto import medir_impactos  # noqa: E402
from bot.notificaciones import Notificador  # noqa: E402

PARES = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT"]
DIAS_HISTORIA = 420
DIAS_PAPER = 30


def velas_sinteticas(n: int, semilla: int, fin: pd.Timestamp, precio0: float) -> pd.DataFrame:
    rng = np.random.default_rng(semilla)
    idx = pd.date_range(end=fin, periods=n, freq="h", tz="UTC")
    rets = rng.normal(0, 0.009, n) + 0.0015 * np.sin(np.arange(n) / 9.0 + semilla)
    cierre = precio0 * np.exp(np.cumsum(rets))
    apertura = np.concatenate([[precio0], cierre[:-1]])
    alto = np.maximum(apertura, cierre) * (1 + rng.uniform(0, 0.004, n))
    bajo = np.minimum(apertura, cierre) * (1 - rng.uniform(0, 0.004, n))
    vol = rng.uniform(50, 150, n) * np.where(rng.random(n) < 0.3, 2.6, 1.0)
    ts = ((idx - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy()
    return pd.DataFrame({"ts": ts, "open": apertura, "high": alto, "low": bajo, "close": cierre, "volume": vol}, index=idx)


class MercadoDemo:
    def __init__(self, velas: dict[str, pd.DataFrame]):
        self.velas = velas
        self.ahora = None

    def actualizar(self, ahora):
        self.ahora = ahora

    def precios(self):
        t = self.ahora.floor("h")
        return {p: float(df.at[t, "open"]) for p, df in self.velas.items() if t in df.index}


class ClaudeFalso:
    """Imita las respuestas de Claude con reglas simples. NO es Claude: solo sirve para poblar el panel."""

    def __init__(self, semilla=7):
        self.rng = np.random.default_rng(semilla)
        self.messages = self

    def parse(self, **kw):
        uso = SimpleNamespace(input_tokens=int(self.rng.integers(2500, 4000)), output_tokens=int(self.rng.integers(400, 900)),
                              cache_read_input_tokens=1200, cache_creation_input_tokens=0)
        if kw["output_format"] is LoteAnalisis:
            return SimpleNamespace(parsed_output=LoteAnalisis(noticias=[]), stop_reason="end_turn", usage=uso)
        texto = kw["messages"][0]["content"]
        par = re.search(r"— (\w+/USDT) —", texto).group(1)
        s = json.loads(re.search(r'\{"direccion".*?\}', texto).group(0))
        conf = float(np.round(self.rng.uniform(0.45, 0.85), 2))
        if conf < 0.55:
            d = DecisionClaude(accion="mantener", par=par, confianza=conf, tamano_sugerido_pct=0, stop_loss=0,
                               take_profit=0, razonamiento="[DEMO] Confirmaciones débiles: prefiero no operar.",
                               hipotesis_que_aplica=[], factores_a_favor=[], factores_en_contra=["[DEMO] volumen justo"])
        else:
            d = DecisionClaude(
                accion="comprar" if s["direccion"] == "largo" else "vender", par=par, confianza=conf,
                tamano_sugerido_pct=100 if conf >= 0.7 else 60, stop_loss=s["stop_loss"], take_profit=s["take_profit"],
                razonamiento=f"[DEMO] Cruce de EMAs con volumen {s['volumen_relativo']}x y RSI {s['rsi']}: apruebo.",
                hipotesis_que_aplica=[], factores_a_favor=[f"[DEMO] volumen {s['volumen_relativo']}x"],
                factores_en_contra=["[DEMO] respuesta simulada, no es Claude"])
        return SimpleNamespace(parsed_output=d, stop_reason="end_turn", usage=uso, _request_id="demo")


def main() -> int:
    ruta = RAIZ / "data" / "demo.db"
    for f in ruta.parent.glob("demo.db*"):
        f.unlink()
    config = cargar_config()
    config.pares = PARES
    s = crear_sesion(crear_motor(ruta))
    fin = pd.Timestamp.now(tz="UTC").floor("h")
    velas = {}
    for k, (par, p0) in enumerate(zip(PARES, (60000, 3000, 150, 0.6, 0.45))):
        df = velas_sinteticas(24 * DIAS_HISTORIA, 100 + k, fin, p0)
        guardar_velas(s, df.reset_index(drop=True), "binance", par, "1h")
        velas[par] = df

    avisos = Notificador()
    broker = BrokerSimulado(config.backtest.comision_pct, config.backtest.slippage_pct)
    carteras = {n: GestorCartera(s, config, n, broker, avisos) for n in (CARTERA_CLAUDE, CARTERA_SOLO)}
    claude = ClienteClaude(config.claude, s, cliente=ClaudeFalso())
    mercado = MercadoDemo(velas)
    ciclo = Ciclo(s, config, mercado, carteras, claude, avisos, lector_rss=lambda url, t: [])
    inicio = fin - pd.Timedelta(days=DIAS_PAPER)
    for t in pd.date_range(inicio, fin - pd.Timedelta(hours=1), freq="h"):
        ciclo.ciclo_horario(t + pd.Timedelta(minutes=1))
    print(f"Paper trading simulado: {DIAS_PAPER} días")

    rng = np.random.default_rng(3)
    temas = ["regulacion", "etf_institucional", "macro_tasas", "hackeo_seguridad", "listado_exchange"]
    for i in range(40):
        t = inicio + pd.Timedelta(hours=int(rng.integers(0, 24 * (DIAS_PAPER - 2))))
        par = PARES[i % len(PARES)].split("/")[0]
        s.add(Noticia(huella=f"demo{i}", fuente="demo", titulo=f"[DEMO] Noticia simulada {i} sobre {par}",
                      url=f"https://ejemplo.invalid/{i}", publicada_ms=int(t.timestamp() * 1000),
                      obtenida_ms=int(t.timestamp() * 1000), analizada=True, relevante=True, activos=json.dumps([par]),
                      resumen="[DEMO] Texto de ejemplo, no es una noticia real.",
                      sentimiento=float(np.round(rng.uniform(-1, 1), 2)), impacto=["bajo", "medio", "alto"][i % 3],
                      horizonte="horas", tema=temas[i % len(temas)]))
    s.commit()
    medir_impactos(s, "binance", PARES, int(fin.timestamp() * 1000))

    motor = MotorAprendizaje(s, config, fin)
    informe = motor.ciclo_semanal(None)
    print(informe.resumen_corto())
    print(f"Base de demostración creada en {ruta}")
    print('Para verla:  $env:PANEL_DB="data/demo.db"; streamlit run panel/app.py')
    return 0


if __name__ == "__main__":
    sys.exit(main())
