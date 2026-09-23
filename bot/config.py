"""Carga y validación de la configuración (config.yaml) y de los secretos (.env)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

RAIZ = Path(__file__).resolve().parent.parent
RUTA_CONFIG = RAIZ / "config" / "config.yaml"

# Frase exacta que debe aparecer en MODO_REAL_AUTORIZADO para permitir dinero real.
FRASE_AUTORIZACION_REAL = "AUTORIZO_OPERAR_CON_DINERO_REAL"


class ErrorConfiguracion(Exception):
    """La configuración es inválida o insegura."""


class ParametroAjustable(BaseModel):
    """Parámetro que el aprendizaje puede modificar, siempre dentro de [min, max]."""

    valor: float
    min: float
    max: float

    @model_validator(mode="after")
    def _dentro_de_rango(self) -> "ParametroAjustable":
        if self.min > self.max:
            raise ValueError(f"min ({self.min}) mayor que max ({self.max})")
        if not self.min <= self.valor <= self.max:
            raise ValueError(f"valor {self.valor} fuera del rango [{self.min}, {self.max}]")
        return self


class ConfigExchange(BaseModel):
    nombre: Literal["binance"]
    tipo_mercado: Literal["spot", "future"]
    demo: bool = True


class ConfigHistorico(BaseModel):
    dias: int = Field(gt=0)


class ConfigCapital(BaseModel):
    simulado_usd: float = Field(gt=0)
    real_usd: float = Field(gt=0)


class ConfigRiesgo(BaseModel):
    sl_usd_por_operacion: float = Field(gt=0)
    riesgo_max_pct_operacion: float = Field(gt=0, le=5)
    max_posiciones: int = Field(ge=1)
    perdida_diaria_max_pct: float = Field(gt=0)
    drawdown_max_pct: float = Field(gt=0)
    confianza_min_claude: float = Field(ge=0, le=1)
    apalancamiento: int = Field(ge=1, le=1)  # fijo en 1x por decisión del dueño
    hora_cierre_diario_utc: str

    @model_validator(mode="after")
    def _hora_valida(self) -> "ConfigRiesgo":
        hh, mm = self.hora_cierre_diario_utc.split(":")
        if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
            raise ValueError("hora_cierre_diario_utc debe tener formato HH:MM")
        return self


class ConfigEstrategia(BaseModel):
    ema_rapida: ParametroAjustable
    ema_lenta: ParametroAjustable
    periodo_volumen: ParametroAjustable
    multiplicador_volumen: ParametroAjustable
    periodo_rsi: ParametroAjustable
    rsi_compra_min: ParametroAjustable
    rsi_sobrecompra: ParametroAjustable
    confianza_min: ParametroAjustable

    @model_validator(mode="after")
    def _emas_coherentes(self) -> "ConfigEstrategia":
        if self.ema_rapida.valor >= self.ema_lenta.valor:
            raise ValueError("ema_rapida debe ser menor que ema_lenta")
        return self


class ConfigClaude(BaseModel):
    modelo: str
    presupuesto_mensual_usd: float = Field(ge=0)


class ConfigNoticias(BaseModel):
    fuentes_rss: list[str]


class ConfigTelegram(BaseModel):
    habilitado: bool = False


class ConfigRutas(BaseModel):
    base_datos: str
    logs: str

    def absoluta(self, ruta: str) -> Path:
        p = Path(ruta)
        return p if p.is_absolute() else RAIZ / p


class Config(BaseModel):
    modo: Literal["paper", "real"]
    exchange: ConfigExchange
    pares: list[str] = Field(min_length=1)
    temporalidad: str
    historico: ConfigHistorico
    capital: ConfigCapital
    riesgo: ConfigRiesgo
    estrategia: ConfigEstrategia
    claude: ConfigClaude
    noticias: ConfigNoticias
    telegram: ConfigTelegram
    rutas: ConfigRutas

    @property
    def capital_operativo(self) -> float:
        return self.capital.real_usd if self.modo == "real" else self.capital.simulado_usd

    def problemas_de_riesgo(self) -> list[str]:
        """Incoherencias entre el SL fijo en USD y el capital del modo actual."""
        problemas = []
        capital = self.capital_operativo
        riesgo_pct = self.riesgo.sl_usd_por_operacion / capital * 100
        if riesgo_pct > self.riesgo.riesgo_max_pct_operacion:
            problemas.append(
                f"El SL de {self.riesgo.sl_usd_por_operacion} USD es el {riesgo_pct:.1f}% de un capital de "
                f"{capital} USD; el máximo permitido es {self.riesgo.riesgo_max_pct_operacion}%."
            )
        if riesgo_pct >= self.riesgo.drawdown_max_pct:
            problemas.append(
                f"Una sola pérdida ({riesgo_pct:.1f}%) alcanzaría el drawdown máximo ({self.riesgo.drawdown_max_pct}%)."
            )
        return problemas


class Secretos(BaseModel):
    binance_api_key: str = ""
    binance_api_secret: str = ""
    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    modo_real_autorizado: str = ""

    @property
    def tiene_claves_exchange(self) -> bool:
        return bool(self.binance_api_key and self.binance_api_secret)


def cargar_secretos(ruta_env: Path | None = None) -> Secretos:
    load_dotenv(ruta_env or RAIZ / ".env", override=False)
    return Secretos(
        binance_api_key=os.getenv("BINANCE_API_KEY", ""),
        binance_api_secret=os.getenv("BINANCE_API_SECRET", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        modo_real_autorizado=os.getenv("MODO_REAL_AUTORIZADO", ""),
    )


def validar_seguridad(config: Config, secretos: Secretos) -> None:
    """Bloquea el arranque si la configuración pone en riesgo dinero real o viola reglas de riesgo."""
    if config.modo == "real":
        if secretos.modo_real_autorizado != FRASE_AUTORIZACION_REAL:
            raise ErrorConfiguracion(
                "modo: real está bloqueado. Requiere autorización explícita del dueño en .env "
                "(MODO_REAL_AUTORIZADO) tras backtesting y 2 semanas de paper trading."
            )
        if config.exchange.demo:
            raise ErrorConfiguracion("modo: real con exchange.demo: true es contradictorio.")
    problemas = config.problemas_de_riesgo()
    if problemas:
        raise ErrorConfiguracion("Reglas de riesgo incoherentes:\n- " + "\n- ".join(problemas))


def cargar_config(ruta: Path | None = None) -> Config:
    with open(ruta or RUTA_CONFIG, encoding="utf-8") as f:
        datos = yaml.safe_load(f)
    return Config.model_validate(datos)
