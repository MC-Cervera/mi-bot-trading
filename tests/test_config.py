import pytest
from pydantic import ValidationError

from bot.config import (
    FRASE_AUTORIZACION_REAL, Config, ErrorConfiguracion, ParametroAjustable, Secretos, validar_seguridad,
)


def test_config_por_defecto_es_valida_y_segura(config):
    assert config.modo == "paper"
    assert config.exchange.demo is True
    assert len(config.pares) == 15
    validar_seguridad(config, Secretos())  # no lanza


def test_sl_de_8_usd_es_0_8_pct_del_capital_simulado(config):
    assert config.capital_operativo == 1000
    assert config.problemas_de_riesgo() == []


def test_modo_real_bloqueado_sin_autorizacion(config_dict):
    config_dict["modo"] = "real"
    config_dict["exchange"]["demo"] = False
    cfg = Config.model_validate(config_dict)
    with pytest.raises(ErrorConfiguracion, match="bloqueado"):
        validar_seguridad(cfg, Secretos())
    with pytest.raises(ErrorConfiguracion, match="bloqueado"):
        validar_seguridad(cfg, Secretos(modo_real_autorizado="si"))


def test_modo_real_con_50_usd_y_sl_8_usd_se_bloquea_por_riesgo(config_dict):
    """8 USD de SL sobre 50 USD es 16% por operación: viola el 1% y el drawdown del 15%."""
    config_dict["modo"] = "real"
    config_dict["exchange"]["demo"] = False
    cfg = Config.model_validate(config_dict)
    with pytest.raises(ErrorConfiguracion, match="incoherentes") as e:
        validar_seguridad(cfg, Secretos(modo_real_autorizado=FRASE_AUTORIZACION_REAL))
    assert "16.0%" in str(e.value)
    assert "drawdown" in str(e.value)


def test_modo_real_no_puede_usar_demo(config_dict):
    config_dict["modo"] = "real"
    cfg = Config.model_validate(config_dict)
    with pytest.raises(ErrorConfiguracion, match="contradictorio"):
        validar_seguridad(cfg, Secretos(modo_real_autorizado=FRASE_AUTORIZACION_REAL))


def test_parametro_ajustable_fuera_de_rango():
    with pytest.raises(ValidationError):
        ParametroAjustable(valor=4, min=5, max=21)


def test_apalancamiento_distinto_de_1_rechazado(config_dict):
    config_dict["riesgo"]["apalancamiento"] = 3
    with pytest.raises(ValidationError):
        Config.model_validate(config_dict)


def test_ema_rapida_debe_ser_menor_que_lenta(config_dict):
    config_dict["estrategia"]["ema_rapida"]["valor"] = 21
    config_dict["estrategia"]["ema_lenta"]["valor"] = 21
    with pytest.raises(ValidationError):
        Config.model_validate(config_dict)


def test_env_example_no_contiene_secretos():
    """.env.example se sube a GitHub: debe tener todas las variables vacías. Las claves reales van SOLO en .env."""
    from bot.config import RAIZ
    for linea in (RAIZ / ".env.example").read_text(encoding="utf-8").splitlines():
        if linea.strip() and not linea.lstrip().startswith("#"):
            nombre, _, valor = linea.partition("=")
            assert valor.strip() == "", f"{nombre} tiene un valor en .env.example: ¡nunca pongas claves reales ahí!"


def test_env_esta_ignorado_por_git():
    from bot.config import RAIZ
    assert ".env" in (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
