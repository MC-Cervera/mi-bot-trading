"""Tablas de la base de datos. En cada fase se añaden las nuevas (señales, operaciones, noticias...)."""
from __future__ import annotations

from sqlalchemy import BigInteger, Float, Index, String, Text, UniqueConstraint
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
