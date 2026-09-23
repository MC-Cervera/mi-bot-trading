"""Tablas de la base de datos. En cada fase se añaden las nuevas (señales, operaciones, noticias...)."""
from __future__ import annotations

from sqlalchemy import BigInteger, Float, Index, String, UniqueConstraint
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
