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


class Operacion(Base):
    """Operación (paper o demo). Rastreable de punta a punta: señal -> decisión de Claude -> regla que la permitió."""

    __tablename__ = "operaciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    id_cliente: Mapped[str] = mapped_column(String(96), unique=True)  # clave de idempotencia: nunca dos veces la misma señal
    cartera: Mapped[str] = mapped_column(String(32), index=True)       # tecnico_claude | tecnico_solo
    par: Mapped[str] = mapped_column(String(32), index=True)
    direccion: Mapped[str] = mapped_column(String(8))
    estado: Mapped[str] = mapped_column(String(12), index=True)        # abierta | cerrada
    ts_senal_ms: Mapped[int] = mapped_column(BigInteger)
    ts_entrada_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    ts_salida_ms: Mapped[int | None] = mapped_column(BigInteger, index=True)
    precio_entrada: Mapped[float] = mapped_column(Float)
    precio_salida: Mapped[float | None] = mapped_column(Float)
    cantidad: Mapped[float] = mapped_column(Float)
    nocional: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    riesgo_usd: Mapped[float] = mapped_column(Float)
    comisiones: Mapped[float] = mapped_column(Float, default=0.0)
    funding: Mapped[float] = mapped_column(Float, default=0.0)
    ultimo_funding_ms: Mapped[int | None] = mapped_column(BigInteger)
    pnl_neto: Mapped[float | None] = mapped_column(Float)
    r_multiple: Mapped[float | None] = mapped_column(Float)
    motivo_salida: Mapped[str | None] = mapped_column(String(32))
    max_favorable: Mapped[float | None] = mapped_column(Float)   # mejor precio alcanzado (para el análisis posterior)
    max_adverso: Mapped[float | None] = mapped_column(Float)     # peor precio alcanzado
    # trazabilidad
    senal_id: Mapped[int | None] = mapped_column(Integer)
    llamada_claude_id: Mapped[int | None] = mapped_column(Integer)
    sugerida_por: Mapped[str] = mapped_column(String(32))        # "senal_tecnica" | "senal_tecnica+claude"
    confianza_claude: Mapped[float | None] = mapped_column(Float)
    lecciones_aplicadas: Mapped[str] = mapped_column(Text, default="[]")
    reglas_que_permitieron: Mapped[str] = mapped_column(Text, default="[]")
    ordenes_exchange: Mapped[str] = mapped_column(Text, default="{}")
    # diario de trading
    diario_entrada: Mapped[str] = mapped_column(Text, default="")
    diario_salida: Mapped[str | None] = mapped_column(Text)
    analisis_post: Mapped[str | None] = mapped_column(Text)


class EventoRiesgo(Base):
    """Bloqueos, pausas, paradas, emergencias y errores de ejecución, con su motivo."""

    __tablename__ = "eventos_riesgo"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    cartera: Mapped[str] = mapped_column(String(32), index=True)
    tipo: Mapped[str] = mapped_column(String(32), index=True)
    par: Mapped[str | None] = mapped_column(String(32))
    detalle: Mapped[str] = mapped_column(Text)
    senal_id: Mapped[int | None] = mapped_column(Integer)
    llamada_claude_id: Mapped[int | None] = mapped_column(Integer)


class EstadoCartera(Base):
    """Estado persistente de cada cartera (sobrevive a reinicios del bot)."""

    __tablename__ = "estado_carteras"

    cartera: Mapped[str] = mapped_column(String(32), primary_key=True)
    capital_inicial: Mapped[float] = mapped_column(Float)
    pico: Mapped[float] = mapped_column(Float)
    estado: Mapped[str] = mapped_column(String(16), default="activo")  # activo | pausado | detenido
    motivo: Mapped[str] = mapped_column(Text, default="")
    pausado_dia: Mapped[str | None] = mapped_column(String(10))       # AAAA-MM-DD UTC de la pausa diaria


class PuntoCapital(Base):
    """Foto del capital (marcado a mercado) para la curva de capital y el drawdown del panel."""

    __tablename__ = "curva_capital"
    __table_args__ = (UniqueConstraint("cartera", "ts_ms", name="uq_punto_capital"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cartera: Mapped[str] = mapped_column(String(32), index=True)
    ts_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    capital: Mapped[float] = mapped_column(Float)
    realizado: Mapped[float] = mapped_column(Float)
    posiciones_abiertas: Mapped[int] = mapped_column(Integer)


class Hipotesis(Base):
    """Algo que el bot sospecha pero aún no ha demostrado. Siempre expresada de forma que el código pueda probarla."""

    __tablename__ = "hipotesis"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(8), unique=True)            # H0001
    enunciado: Mapped[str] = mapped_column(Text)
    explicacion: Mapped[str] = mapped_column(Text, default="")             # en lenguaje claro, para aprender
    origen: Mapped[str] = mapped_column(String(16))                        # estadistico | claude
    tipo: Mapped[str] = mapped_column(String(12))                          # filtro | parametro
    condiciones: Mapped[str] = mapped_column(Text, default="[]")           # JSON (tipo filtro)
    parametro: Mapped[str | None] = mapped_column(String(32))              # tipo parametro
    valor_propuesto: Mapped[float | None] = mapped_column(Float)
    efecto_esperado: Mapped[str] = mapped_column(String(8))                # mejor | peor
    metrica: Mapped[str] = mapped_column(String(64), default="R medio por operación (grupo vs. control)")
    muestra_minima: Mapped[int] = mapped_column(Integer, default=30)
    estado: Mapped[str] = mapped_column(String(16), index=True)            # propuesta | en_prueba | validada | descartada
    motivo_estado: Mapped[str] = mapped_column(Text, default="")
    creada_ms: Mapped[int] = mapped_column(BigInteger)
    actualizada_ms: Mapped[int] = mapped_column(BigInteger)
    evidencia_backtest: Mapped[str | None] = mapped_column(Text)           # JSON
    evidencia_adelante: Mapped[str | None] = mapped_column(Text)           # JSON (datos posteriores a su creación)
    firma: Mapped[str] = mapped_column(String(200), index=True)            # para no proponer dos veces lo mismo


class Leccion(Base):
    """Hipótesis que pasó la prueba. Se pasa a Claude en cada decisión mientras esté vigente."""

    __tablename__ = "lecciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(8), unique=True)            # L0001
    hipotesis_id: Mapped[int] = mapped_column(ForeignKey("hipotesis.id"))
    enunciado: Mapped[str] = mapped_column(Text)
    explicacion: Mapped[str] = mapped_column(Text, default="")
    evidencia: Mapped[str] = mapped_column(Text)                           # JSON: n, acierto, R medio, control, p, fechas
    estado: Mapped[str] = mapped_column(String(16), index=True)            # vigente | en_revision | refutada
    motivo_estado: Mapped[str] = mapped_column(Text, default="")
    validada_ms: Mapped[int] = mapped_column(BigInteger)
    revisada_ms: Mapped[int | None] = mapped_column(BigInteger)
    ajuste_id: Mapped[int | None] = mapped_column(Integer)


class AjusteParametro(Base):
    """Cambio del bot a su propia configuración. Siempre dentro de los rangos del dueño y reversible."""

    __tablename__ = "ajustes_parametros"

    id: Mapped[int] = mapped_column(primary_key=True)
    parametro: Mapped[str] = mapped_column(String(32), index=True)
    valor_anterior: Mapped[float] = mapped_column(Float)
    valor_nuevo: Mapped[float] = mapped_column(Float)
    justificacion: Mapped[str] = mapped_column(Text)
    leccion_id: Mapped[int | None] = mapped_column(Integer)
    creado_ms: Mapped[int] = mapped_column(BigInteger)
    revertido_ms: Mapped[int | None] = mapped_column(BigInteger)
    motivo_reversion: Mapped[str | None] = mapped_column(Text)


class HistorialAprendizaje(Base):
    """Todo lo que le pasa a una hipótesis, lección o ajuste, en orden y en lenguaje claro."""

    __tablename__ = "historial_aprendizaje"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    entidad: Mapped[str] = mapped_column(String(16))                       # hipotesis | leccion | ajuste | revision
    entidad_id: Mapped[int | None] = mapped_column(Integer, index=True)
    evento: Mapped[str] = mapped_column(String(32))
    detalle: Mapped[str] = mapped_column(Text)
