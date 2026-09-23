"""Tablas de la base de datos. En cada fase se añaden las nuevas (señales, operaciones, noticias...)."""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Vela(Base):
    """Vela OHLCV cerrada. `ts` es la hora de APERTURA en milisegundos UTC."""

    __tablename__ = "velas"
    __table_args__ = (
        UniqueConstraint("exchange", "par", "temporalidad", "ts", name="uq_vela"),
        Index("ix_velas_par_tf_ts", "par", "temporalidad", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    exchange: Mapped[str] = mapped_column(String(32))
    par: Mapped[str] = mapped_column(String(32))
    temporalidad: Mapped[str] = mapped_column(String(8))
    ts: Mapped[int] = mapped_column(BigInteger)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)


class Senal(Base):
    """Cruce de EMAs detectado, confirmado o descartado (los descartados son el grupo de control)."""

    __tablename__ = "senales"
    __table_args__ = (UniqueConstraint("exchange", "par", "temporalidad", "ts", "parametros", name="uq_senal"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    exchange: Mapped[str] = mapped_column(String(32))
    par: Mapped[str] = mapped_column(String(32), index=True)
    temporalidad: Mapped[str] = mapped_column(String(8))
    ts: Mapped[int] = mapped_column(BigInteger, index=True)  # apertura de la vela del cruce (ms UTC)
    direccion: Mapped[str] = mapped_column(String(8))  # largo | corto
    estado: Mapped[str] = mapped_column(String(16))  # confirmada | descartada
    motivo: Mapped[str] = mapped_column(Text)
    precio: Mapped[float] = mapped_column(Float)
    ema_rapida: Mapped[float | None] = mapped_column(Float)
    ema_lenta: Mapped[float | None] = mapped_column(Float)
    vol_rel: Mapped[float | None] = mapped_column(Float)
    rsi: Mapped[float | None] = mapped_column(Float)
    atr: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float)
    parametros: Mapped[str] = mapped_column(Text)  # JSON con los parámetros usados


class Noticia(Base):
    """Noticia obtenida de una fuente. Los campos de análisis los rellena Claude (None hasta entonces)."""

    __tablename__ = "noticias"

    id: Mapped[int] = mapped_column(primary_key=True)
    huella: Mapped[str] = mapped_column(String(64), unique=True)  # hash de la URL: evita duplicados
    fuente: Mapped[str] = mapped_column(String(128))
    titulo: Mapped[str] = mapped_column(Text)
    resumen_original: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text)
    publicada_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    obtenida_ms: Mapped[int] = mapped_column(BigInteger, index=True)  # cuándo la vio el bot (para decidir)
    activos_detectados: Mapped[str] = mapped_column(Text, default="[]")  # JSON, filtro por palabras clave
    # análisis de Claude
    analizada: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    relevante: Mapped[bool | None] = mapped_column(Boolean)
    resumen: Mapped[str | None] = mapped_column(Text)
    activos: Mapped[str | None] = mapped_column(Text)  # JSON: activos afectados según Claude
    sentimiento: Mapped[float | None] = mapped_column(Float)  # -1 a 1
    impacto: Mapped[str | None] = mapped_column(String(8))  # bajo | medio | alto
    horizonte: Mapped[str | None] = mapped_column(String(16))  # horas | dias | semanas
    tema: Mapped[str | None] = mapped_column(String(32), index=True)


class ImpactoNoticia(Base):
    """Movimiento real del precio después de una noticia, por activo. Base de las estadísticas de impacto."""

    __tablename__ = "impacto_noticias"
    __table_args__ = (UniqueConstraint("noticia_id", "par", name="uq_impacto"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    noticia_id: Mapped[int] = mapped_column(ForeignKey("noticias.id"), index=True)
    par: Mapped[str] = mapped_column(String(32))
    precio_inicial: Mapped[float] = mapped_column(Float)
    ret_1h: Mapped[float | None] = mapped_column(Float)   # % del par
    ret_4h: Mapped[float | None] = mapped_column(Float)
    ret_24h: Mapped[float | None] = mapped_column(Float)
    anormal_4h: Mapped[float | None] = mapped_column(Float)   # % del par menos % medio del mercado
    anormal_24h: Mapped[float | None] = mapped_column(Float)
    completo: Mapped[bool] = mapped_column(Boolean, default=False)  # True cuando ya pasaron 24 h


class LlamadaClaude(Base):
    """Cada llamada a la API de Claude: para qué, qué se envió, qué respondió y cuánto costó."""

    __tablename__ = "llamadas_claude"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    proposito: Mapped[str] = mapped_column(String(24))  # decision | noticias
    disparador: Mapped[str | None] = mapped_column(String(24))  # senal | noticia_alto_impacto
    par: Mapped[str | None] = mapped_column(String(32), index=True)
    senal_id: Mapped[int | None] = mapped_column(Integer)
    modelo: Mapped[str] = mapped_column(String(64))
    estado: Mapped[str] = mapped_column(String(24))  # ok | error | presupuesto_agotado | rechazo | invalida
    contexto: Mapped[str] = mapped_column(Text)       # lo que se le envió (texto completo)
    respuesta: Mapped[str | None] = mapped_column(Text)  # JSON validado o texto del error
    tokens_entrada: Mapped[int] = mapped_column(Integer, default=0)
    tokens_salida: Mapped[int] = mapped_column(Integer, default=0)
    tokens_cache_lectura: Mapped[int] = mapped_column(Integer, default=0)
    tokens_cache_escritura: Mapped[int] = mapped_column(Integer, default=0)
    costo_usd: Mapped[float] = mapped_column(Float, default=0.0)
    id_solicitud: Mapped[str | None] = mapped_column(String(64))
