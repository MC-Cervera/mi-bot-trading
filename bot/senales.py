"""Generación de señales técnicas candidatas.

Regla base (evaluada al CIERRE de cada vela; la entrada sería en la apertura de la siguiente):

LARGO  = EMA rápida cruza por ENCIMA de la lenta
         + volumen relativo >= multiplicador_volumen
         + RSI en [rsi_compra_min, rsi_sobrecompra)          (ej. 40-70: impulso sin sobrecompra)
CORTO  = EMA rápida cruza por DEBAJO de la lenta
         + volumen relativo >= multiplicador_volumen
         + RSI en (100 - rsi_sobrecompra, 100 - rsi_compra_min]   (ej. 30-60: simétrico)
Además, la entrada debe quedar al menos `horas_minimas_antes_cierre` antes del cierre diario (23:00 UTC).

Stop loss técnico = entrada -/+ atr_mult_sl x ATR. Take profit = ratio_tp x distancia del SL.
El tamaño de la posición (para que el SL cueste 8 USD) lo calcula la capa de riesgo en la Fase 5.

Los cruces que NO pasan todas las confirmaciones también se registran con su motivo: sirven como
grupo de control para el sistema de aprendizaje.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sqlalchemy.dialects.sqlite import insert

from bot.config import Config
from bot.datos.historico import ms_temporalidad
from bot.db.modelos import Senal
from bot.db.sesion import filas_por_bloque
from bot.indicadores import ParametrosIndicadores, calcular_indicadores

LARGO, CORTO, NINGUNA = 1, -1, 0
NOMBRE_DIRECCION = {LARGO: "largo", CORTO: "corto"}


@dataclass(frozen=True)
class ParametrosSenal:
    indicadores: ParametrosIndicadores = field(default_factory=ParametrosIndicadores)
    multiplicador_volumen: float = 1.5
    rsi_compra_min: float = 40
    rsi_sobrecompra: float = 70
    atr_mult_sl: float = 1.5
    ratio_tp: float = 2.0
    hora_cierre_utc: str = "23:00"
    horas_minimas_antes_cierre: int = 2

    @classmethod
    def desde_config(cls, config: Config) -> "ParametrosSenal":
        e = config.estrategia
        return cls(
            indicadores=ParametrosIndicadores(
                ema_rapida=int(e.ema_rapida.valor),
                ema_lenta=int(e.ema_lenta.valor),
                periodo_volumen=int(e.periodo_volumen.valor),
                periodo_rsi=int(e.periodo_rsi.valor),
            ),
            multiplicador_volumen=e.multiplicador_volumen.valor,
            rsi_compra_min=e.rsi_compra_min.valor,
            rsi_sobrecompra=e.rsi_sobrecompra.valor,
            atr_mult_sl=e.atr_mult_sl.valor,
            ratio_tp=e.ratio_tp.valor,
            hora_cierre_utc=config.riesgo.hora_cierre_diario_utc,
            horas_minimas_antes_cierre=config.senales.horas_minimas_antes_cierre,
        )

    @property
    def zona_rsi_largo(self) -> tuple[float, float]:
        return self.rsi_compra_min, self.rsi_sobrecompra

    @property
    def zona_rsi_corto(self) -> tuple[float, float]:
        return 100 - self.rsi_sobrecompra, 100 - self.rsi_compra_min

    @property
    def minuto_cierre(self) -> int:
        hh, mm = self.hora_cierre_utc.split(":")
        return int(hh) * 60 + int(mm)


def entrada_en_horario(hora_entrada: pd.DatetimeIndex, p: ParametrosSenal) -> np.ndarray:
    """True si entre la hora de entrada y el cierre diario quedan al menos las horas mínimas."""
    minutos = hora_entrada.hour * 60 + hora_entrada.minute
    return np.asarray(minutos + p.horas_minimas_antes_cierre * 60 <= p.minuto_cierre)


def generar_senales(
    velas: pd.DataFrame, p: ParametrosSenal, temporalidad: str = "1h",
    indicadores: pd.DataFrame | None = None, con_motivos: bool = True,
) -> pd.DataFrame:
    """Calcula indicadores y marca cruces, confirmaciones y señales. Índice: fecha UTC de APERTURA de la vela.

    Columnas añadidas: cruce, conf_volumen, conf_rsi, en_horario, senal, sl, tp, motivo.
    `indicadores`: resultado previo de calcular_indicadores con los mismos periodos (acelera la optimización).
    `con_motivos=False` omite el texto de descarte (el backtest no lo necesita).
    """
    df = indicadores.copy() if indicadores is not None else calcular_indicadores(velas, p.indicadores)
    dif = df["ema_rapida"] - df["ema_lenta"]
    dif_prev = dif.shift(1)
    cruce = np.where((dif_prev <= 0) & (dif > 0), LARGO, np.where((dif_prev >= 0) & (dif < 0), CORTO, NINGUNA))
    df["cruce"] = cruce

    lo_l, hi_l = p.zona_rsi_largo
    lo_c, hi_c = p.zona_rsi_corto
    df["conf_volumen"] = df["vol_rel"] >= p.multiplicador_volumen
    df["conf_rsi"] = np.where(
        cruce == LARGO, (df["rsi"] >= lo_l) & (df["rsi"] < hi_l),
        np.where(cruce == CORTO, (df["rsi"] > lo_c) & (df["rsi"] <= hi_c), False),
    )
    hora_entrada = df.index + pd.Timedelta(milliseconds=ms_temporalidad(temporalidad))
    df["en_horario"] = entrada_en_horario(hora_entrada, p)

    valido = df[["ema_rapida", "ema_lenta", "vol_rel", "rsi", "atr"]].notna().all(axis=1)
    confirmada = (cruce != NINGUNA) & valido & df["conf_volumen"] & df["conf_rsi"] & df["en_horario"]
    df["senal"] = np.where(confirmada, cruce, NINGUNA)

    distancia = p.atr_mult_sl * df["atr"]
    df["sl"] = np.where(cruce == LARGO, df["close"] - distancia, np.where(cruce == CORTO, df["close"] + distancia, np.nan))
    df["tp"] = np.where(
        cruce == LARGO, df["close"] + p.ratio_tp * distancia,
        np.where(cruce == CORTO, df["close"] - p.ratio_tp * distancia, np.nan),
    )

    motivos = np.full(len(df), "", dtype=object)
    for i in (np.flatnonzero(cruce != NINGUNA) if con_motivos else []):
        motivos[i] = _motivo(df.iloc[i], bool(valido.iloc[i]), p)
    df["motivo"] = motivos
    return df


def _motivo(fila: pd.Series, valido: bool, p: ParametrosSenal) -> str:
    if not valido:
        return "descartada: indicadores en calentamiento"
    fallos = []
    if not fila["conf_volumen"]:
        fallos.append(f"volumen {fila['vol_rel']:.2f}x < {p.multiplicador_volumen}x")
    if not fila["conf_rsi"]:
        lo, hi = p.zona_rsi_largo if fila["cruce"] == LARGO else p.zona_rsi_corto
        fallos.append(f"RSI {fila['rsi']:.1f} fuera de zona [{lo:.0f}, {hi:.0f}]")
    if not fila["en_horario"]:
        fallos.append(f"entrada a menos de {p.horas_minimas_antes_cierre}h del cierre diario {p.hora_cierre_utc} UTC")
    return "confirmada" if not fallos else "descartada: " + "; ".join(fallos)


@dataclass
class SenalCandidata:
    """Señal confirmada lista para pasar a Claude / capa de riesgo. Incluye todo para el diario."""

    par: str
    temporalidad: str
    ts_vela: pd.Timestamp
    direccion: str
    precio_referencia: float
    stop_loss: float
    take_profit: float
    ema_rapida: float
    ema_lenta: float
    vol_rel: float
    rsi: float
    atr: float
    parametros: dict

    def explicar(self) -> str:
        """Texto en lenguaje claro para el diario de trading."""
        flecha = "por encima" if self.direccion == "largo" else "por debajo"
        riesgo_pct = abs(self.precio_referencia - self.stop_loss) / self.precio_referencia * 100
        return (
            f"{self.par} {self.temporalidad} — vela de {self.ts_vela:%Y-%m-%d %H:%M} UTC. "
            f"La EMA rápida ({self.ema_rapida:.4f}) cruzó {flecha} de la lenta ({self.ema_lenta:.4f}): cambio de tendencia "
            f"de corto plazo hacia {'arriba' if self.direccion == 'largo' else 'abajo'}. "
            f"Confirmación de volumen: {self.vol_rel:.2f}x el promedio (mínimo {self.parametros['multiplicador_volumen']}x), "
            f"hay participación real detrás del movimiento. RSI {self.rsi:.1f}: "
            + ("hay impulso sin estar sobrecomprado. " if self.direccion == "largo" else "hay debilidad sin estar sobrevendido. ")
            + f"Propuesta: {self.direccion.upper()} cerca de {self.precio_referencia:.4f}, stop loss {self.stop_loss:.4f} "
            f"({riesgo_pct:.2f}% de distancia, {self.parametros['atr_mult_sl']}x ATR), take profit {self.take_profit:.4f} "
            f"(relación {self.parametros['ratio_tp']}:1)."
        )


def extraer_candidatas(df_senales: pd.DataFrame, par: str, p: ParametrosSenal, temporalidad: str = "1h") -> list[SenalCandidata]:
    parametros = {
        "ema_rapida": p.indicadores.ema_rapida, "ema_lenta": p.indicadores.ema_lenta,
        "periodo_volumen": p.indicadores.periodo_volumen, "periodo_rsi": p.indicadores.periodo_rsi,
        "multiplicador_volumen": p.multiplicador_volumen, "rsi_compra_min": p.rsi_compra_min,
        "rsi_sobrecompra": p.rsi_sobrecompra, "atr_mult_sl": p.atr_mult_sl, "ratio_tp": p.ratio_tp,
    }
    return [
        SenalCandidata(
            par=par, temporalidad=temporalidad, ts_vela=ts, direccion=NOMBRE_DIRECCION[int(f["senal"])],
            precio_referencia=float(f["close"]), stop_loss=float(f["sl"]), take_profit=float(f["tp"]),
            ema_rapida=float(f["ema_rapida"]), ema_lenta=float(f["ema_lenta"]), vol_rel=float(f["vol_rel"]),
            rsi=float(f["rsi"]), atr=float(f["atr"]), parametros=parametros,
        )
        for ts, f in df_senales[df_senales["senal"] != NINGUNA].iterrows()
    ]


def guardar_senales(sesion, df_senales: pd.DataFrame, exchange: str, par: str, p: ParametrosSenal, temporalidad: str = "1h") -> int:
    """Guarda todos los cruces (confirmados y descartados). Idempotente."""
    parametros = json.dumps(
        {"indicadores": p.indicadores.__dict__, "multiplicador_volumen": p.multiplicador_volumen,
         "rsi_compra_min": p.rsi_compra_min, "rsi_sobrecompra": p.rsi_sobrecompra,
         "atr_mult_sl": p.atr_mult_sl, "ratio_tp": p.ratio_tp,
         "horas_minimas_antes_cierre": p.horas_minimas_antes_cierre},
        sort_keys=True,
    )

    def num(x):
        return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)

    cruces = df_senales[df_senales["cruce"] != NINGUNA]
    registros = [
        {
            "exchange": exchange, "par": par, "temporalidad": temporalidad, "ts": int(f["ts"]),
            "direccion": NOMBRE_DIRECCION[int(f["cruce"])],
            "estado": "confirmada" if f["senal"] != NINGUNA else "descartada",
            "motivo": f["motivo"], "precio": float(f["close"]),
            **{k: num(f[k]) for k in ("ema_rapida", "ema_lenta", "vol_rel", "rsi", "atr")},
            "stop_loss": num(f["sl"]), "take_profit": num(f["tp"]), "parametros": parametros,
        }
        for _, f in cruces.iterrows()
    ]
    if registros:
        bloque = filas_por_bloque(len(registros[0]))
        for i in range(0, len(registros), bloque):
            sesion.execute(insert(Senal).values(registros[i: i + bloque]).on_conflict_do_nothing())
        sesion.commit()
    return len(registros)
