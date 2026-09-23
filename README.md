# Mi bot de trading

Bot de trading de criptomonedas en Python: análisis técnico (EMA, volumen, RSI) + noticias + Claude como asesor,
con una capa de riesgo en código que tiene la última palabra. **Prioridad n.º 1: proteger el capital.**

> Estado: **Fase 1 de 8** (estructura, configuración, conexión de solo lectura, histórico).
> El README completo llegará en la Fase 8.

## Decisiones acordadas

| Tema | Decisión |
|---|---|
| Exchange | Binance, futuros perpetuos USDⓈ-M, **apalancamiento fijo 1x**. Órdenes en **Binance Demo Trading**. Bitso y Exness (vía MetaTrader 5, solo Windows) más adelante. FXIFY/FTMO fuera. |
| Pares | 15 pares contra USDT (ver `config/config.yaml`) |
| Temporalidad | 1h. Largos y cortos. Todo se cierra a las **23:00 UTC** y no se abre nada hasta las 00:00 UTC |
| Capital de simulación | 1000 USD |
| Stop loss | Obligatorio, 8 USD de pérdida máxima por operación (= 0.8% de 1000 USD) |
| Claude | Sonnet (`claude-sonnet-5`), presupuesto mensual configurable |
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

## Uso (Fase 1)

```powershell
python scripts/verificar_conexion.py     # comprueba configuración, datos públicos y (si hay claves) saldo y permisos
python scripts/descargar_historico.py    # descarga ~2 años de velas 1h de los 15 pares a data/bot.db
python -m pytest                          # pruebas
```

La descarga es incremental: al volver a ejecutarla solo baja las velas nuevas. No necesita claves.

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
bot/db/                 modelos SQLAlchemy y sesión
scripts/                comandos de línea
tests/                  pruebas pytest
```

## Reglas de seguridad ya aplicadas en código

- `modo: real` se niega a arrancar sin `MODO_REAL_AUTORIZADO=AUTORIZO_OPERAR_CON_DINERO_REAL` en `.env`.
- `modo: real` con `demo: true` (o `demo: false` fuera de real) es rechazado.
- Si el SL en USD supera el % de riesgo permitido del capital, el bot no arranca.
- Apalancamiento distinto de 1x es rechazado.
- Las velas aún abiertas se descartan (evita sesgo de anticipación).
