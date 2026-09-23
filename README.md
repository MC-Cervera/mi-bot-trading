# Mi bot de trading

Bot de trading de criptomonedas en Python: análisis técnico (EMA, volumen, RSI) + noticias + Claude como asesor,
con una capa de riesgo en código que tiene la última palabra. **Prioridad n.º 1: proteger el capital.**

> Estado: **Fase 4 de 8** completada (noticias y capa de decisión con Claude).
> El README completo llegará en la Fase 8.

## Decisiones acordadas

| Tema | Decisión |
|---|---|
| Exchange | Binance, futuros perpetuos USDⓈ-M, **apalancamiento fijo 1x**. Órdenes en **Binance Demo Trading**. Bitso y Exness (vía MetaTrader 5, solo Windows) más adelante. FXIFY/FTMO fuera. |
| Pares | 15 pares contra USDT (ver `config/config.yaml`) |
| Temporalidad | 1h. Largos y cortos. Todo se cierra a las **23:00 UTC** y no se abre nada hasta las 00:00 UTC |
| Capital de simulación | 1000 USD |
| Stop loss | Obligatorio, 8 USD de pérdida máxima por operación (= 0.8% de 1000 USD) |
| Claude | Sonnet (`claude-sonnet-5`), presupuesto máximo 15 USD/mes |
| Noticias | RSS de CoinDesk, Cointelegraph, Decrypt. Se medirá el impacto real de cada noticia en el precio |
| Avisos | Telegram (requiere crear un bot con @BotFather) |
| Comparación Claude vs. técnico | Solo en paper trading en paralelo (un backtest con Claude estaría contaminado) |

### Aviso importante sobre la cuenta real de ~50 USD

Con 50 USD, un SL de 8 USD es el **16% de la cuenta por operación**: viola el límite del 1% por operación y una sola
pérdida superaría el drawdown máximo del 15%. El código **bloquea** esta combinación (`validar_seguridad`).
Antes de pasar a real habrá que decidir entre subir el capital (≥ 800 USD para 8 USD al 1%) o reducir el SL en USD.

## Instalación en Windows 11

Requisitos: [Python 3.11+](https://www.python.org/downloads/) (marca "Add python.exe to PATH") y Git.

```powershell
git clone https://github.com/MC-Cervera/mi-bot-trading.git
cd mi-bot-trading
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1        # si PowerShell lo bloquea: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
copy .env.example .env            # y rellena las claves (opcional en Fase 1)
```

## Uso

```powershell
python scripts/verificar_conexion.py     # comprueba configuración, datos públicos y (si hay claves) saldo y permisos
python scripts/descargar_historico.py    # descarga ~2 años de velas 1h de los 15 pares a data/bot.db
python scripts/escanear_senales.py --ultimas 2   # cuántas señales da la estrategia por par y por qué
python scripts/backtest.py               # backtest walk-forward (~1-3 min); informe en reportes/
python scripts/noticias.py               # lee RSS, analiza con Claude, mide impacto real y muestra estadísticas
python scripts/probar_decision.py --par BTC/USDT            # contexto que recibiría Claude (gratis)
python scripts/probar_decision.py --par BTC/USDT --llamar   # consulta real a Claude (~0.01-0.05 USD)
python -m pytest                          # pruebas
```

La descarga es incremental: al volver a ejecutarla solo baja las velas nuevas. No necesita claves.

## Estrategia técnica (Fase 2)

Se evalúa al **cierre** de cada vela de 1h; la entrada sería en la apertura de la siguiente.

| | Largo | Corto |
|---|---|---|
| Tendencia | EMA 9 cruza **encima** de EMA 21 | EMA 9 cruza **debajo** de EMA 21 |
| Volumen | ≥ 1.5x el promedio de las 20 velas anteriores | igual |
| RSI (14) | entre 40 y 70 (impulso sin sobrecompra) | entre 30 y 60 (debilidad sin sobreventa) |
| Horario | la entrada debe quedar ≥ 2 h antes del cierre diario de las 23:00 UTC | igual |
| Stop loss técnico | entrada − 1.5 × ATR(14) | entrada + 1.5 × ATR(14) |
| Take profit | 2 × la distancia del stop | igual |

Los cruces que no cumplen todo se guardan como **descartados** con su motivo: son el grupo de control del aprendizaje.
Los indicadores están implementados a mano (sin pandas-ta) y validados contra los ejemplos de referencia de
StockCharts. Hay pruebas que demuestran que ni indicadores ni señales usan datos del futuro.

## Backtesting (Fase 3)

`python scripts/backtest.py` genera en `reportes/` un informe en español (`backtest_FECHA.md`), las operaciones en CSV
y las curvas de capital en CSV. Compara, **solo en meses que el optimizador no vio**:
estrategia optimizada (walk-forward) vs. parámetros por defecto vs. comprar y mantener (15 pares y solo BTC).

- **Walk-forward:** 6 meses de entrenamiento → 2 meses de prueba → avanzar 2 meses. En entrenamiento se elige la mejor de
  81 combinaciones (dentro de tus rangos) exigiendo al menos 30 operaciones; se evalúa en los 2 meses siguientes.
- **Supuestos conservadores:** entrada en la vela siguiente a la señal; comisión 0.05% por lado; deslizamiento 0.05%;
  funding siempre como costo; si una vela toca stop y objetivo, se asume el stop; huecos se ejecutan a la apertura.
- **Reglas de riesgo incluidas:** 8 USD por operación (o 1% si la cuenta baja de 800), 3 posiciones, nocional ≤ capital/3,
  pausa diaria al −3%, parada definitiva al −15% desde el máximo (se mantiene entre ventanas), cierre a las 23:00 UTC.
- `--rapido` usa una rejilla reducida; `--pares BTC/USDT ETH/USDT` limita los pares.

## Noticias y Claude (Fase 4)

**Noticias:** se leen los RSS configurados; un filtro por palabras clave (gratis) descarta las que no mencionan
ninguno de los 15 activos ni temas de mercado (Fed, SEC, ETF…). Solo las demás se envían a Claude, en lotes, que
devuelve resumen en español, activos afectados, sentimiento (−1 a 1), impacto (bajo/medio/alto), horizonte y tema.

**Impacto real:** para cada noticia relevante se mide cuánto se movió el precio 1 h, 4 h y 24 h después y cuánto
fue movimiento propio del activo (descontando el promedio del mercado). Con eso se arma una tabla por tema e
impacto (¿las noticias de "regulación" de impacto alto mueven de verdad el precio? ¿hacia donde decía el
sentimiento?). Claude recibe ese historial cuando llega una noticia parecida. Se acumula desde que el bot corre:
las fuentes RSS gratuitas no traen noticias antiguas.

**Decisión:** Claude solo se consulta cuando hay una señal técnica confirmada, o cuando una noticia de impacto
alto afecta a un activo con posición abierta. Recibe señal, últimas 24 velas, noticias de las últimas 24 h
(solo las que el bot ya había visto en ese momento), impacto histórico, posiciones, saldo, reglas y lecciones.
Responde en JSON validado con pydantic (el esquema acordado más `factores_a_favor` / `factores_en_contra` para el
diario). Antes de la capa de riesgo se bloquea, con motivo registrado, cualquier propuesta que:
abra contra la dirección de la señal · abra sin señal técnica (una noticia solo puede cerrar) · ponga SL/TP en el
lado equivocado · cierre una posición inexistente · cite lecciones que no existen · hable de otro par.

**Costos:** cada llamada queda registrada (contexto, respuesta, tokens, USD). Si el gasto del mes llega a 15 USD,
el bot deja de llamar a Claude y **no abre operaciones**. Cualquier error, rechazo o respuesta inválida = no operar.
El prompt de sistema es fijo y se cachea para abaratar las llamadas.

### Clave de Anthropic

Créala en https://console.anthropic.com → API Keys y ponla en `.env` como `ANTHROPIC_API_KEY=` (nunca en
`.env.example`). Te recomiendo fijar también un límite de gasto mensual en la consola de Anthropic como segunda barrera.

### Claves de Binance Demo Trading (opcional en Fase 1)

1. Entra en https://demo.binance.com → API Management → crea una clave.
2. Permisos: **solo lectura y trading. Nunca retiros.**
3. Cópialas en `.env` (`BINANCE_API_KEY`, `BINANCE_API_SECRET`). El archivo `.env` nunca se sube al repositorio.

## Estructura

```
config/config.yaml      configuración (riesgo fijo + parámetros ajustables con rangos min/max)
bot/config.py           carga y validación; candado de dinero real
bot/datos/exchange.py   clientes ccxt (datos públicos y cuenta demo), saldo, permisos
bot/datos/historico.py  descarga paginada, velas cerradas únicamente, huecos, upsert en SQLite
bot/indicadores.py      EMA, RSI de Wilder, volumen relativo, ATR
bot/senales.py          reglas de señal, motivos de descarte, explicación para el diario
bot/dimensionamiento.py tamaño de posición para que el stop cueste lo planificado
bot/backtest/           motor vela a vela, métricas, comprar y mantener, walk-forward, informe
bot/noticias/           fuentes RSS, filtro por activo, análisis con Claude, impacto real y estadísticas
bot/ia/                 cliente de Claude (presupuesto y costos), contexto y capa de decisión
bot/db/                 modelos SQLAlchemy (velas, señales, noticias, impacto, llamadas a Claude) y sesión
scripts/                comandos de línea
tests/                  pruebas pytest
```

## Reglas de seguridad ya aplicadas en código

- `modo: real` se niega a arrancar sin `MODO_REAL_AUTORIZADO=AUTORIZO_OPERAR_CON_DINERO_REAL` en `.env`.
- `modo: real` con `demo: true` (o `demo: false` fuera de real) es rechazado.
- Si el SL en USD supera el % de riesgo permitido del capital, el bot no arranca.
- Apalancamiento distinto de 1x es rechazado.
- Las velas aún abiertas se descartan (evita sesgo de anticipación).
