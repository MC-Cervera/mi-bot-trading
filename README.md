# Mi bot de trading

Bot de trading intradía de criptomonedas en Python que combina **análisis técnico** (EMA, volumen y RSI),
**noticias** y a **Claude** como asesor. Una **capa de reglas de riesgo en código tiene siempre la última palabra**,
y el bot **aprende** de sus resultados solo cuando hay evidencia estadística.

> **Prioridad número 1: proteger el capital.** Un bot que no pierde dinero es mejor que uno que gana mucho a veces
> y lo pierde todo después.

> ⚠️ **Estado: PAPER TRADING (dinero simulado).** El modo real está **bloqueado en el código**. Solo se habilitará
> con tu autorización explícita, después de un backtest rentable, al menos 2 semanas de paper trading aceptables y
> el checklist en verde (ver [Paso a dinero real](#paso-a-dinero-real)). Nada de lo que hay aquí garantiza ganancias.

---

## Índice

1. [Qué hace, en una imagen](#qué-hace-en-una-imagen)
2. [Instalación (Windows 11)](#instalación-windows-11)
3. [Configuración: claves y avisos](#configuración-claves-y-avisos)
4. [Primeros pasos, en orden](#primeros-pasos-en-orden)
5. [Uso diario](#uso-diario)
6. [La estrategia](#la-estrategia)
7. [Reglas de riesgo](#reglas-de-riesgo)
8. [Claude y las noticias](#claude-y-las-noticias)
9. [Aprendizaje](#aprendizaje)
10. [Panel web](#panel-web)
11. [Servidor VPS](#servidor-vps)
    - [Exness (MetaTrader 5)](#exness-metatrader-5)
12. [Paso a dinero real](#paso-a-dinero-real)
13. [Costos](#costos)
14. [Limitaciones honestas](#limitaciones-honestas)
15. [Preguntas frecuentes](#preguntas-frecuentes)
16. [Estructura del proyecto](#estructura-del-proyecto)

---

## Qué hace, en una imagen

```
 cada hora (vela de 1 h recién cerrada)
 ┌──────────────┐    ┌──────────────────┐    ┌───────────────┐    ┌──────────────┐
 │ Velas de     │ →  │ Señal técnica    │ →  │ Claude        │ →  │ Capa de      │ → orden (paper)
 │ Binance      │    │ EMA + vol + RSI  │    │ (propone)     │    │ riesgo       │   + diario
 └──────────────┘    └──────────────────┘    └───────────────┘    │ (DECIDE)     │   + Telegram
                              │                     ↑             └──────────────┘
                              │              noticias + impacto
                              │              histórico + lecciones
                              └──────────────────────────────────→ capa de riesgo → orden (paper)
                                  cartera de CONTROL, sin Claude: sirve para medir si Claude aporta algo
```

- **Dos carteras de 1000 USD simulados** reciben exactamente las mismas señales y precios. Una pasa por Claude y la
  otra no. Comparándolas se sabe, con datos, si Claude mejora los resultados o solo cuesta dinero.
- **Cada minuto** se vigilan los stop loss y objetivos. **A las 23:00 UTC (17:00 en Ciudad de México)** se cierra
  todo: ninguna operación pasa de un día a otro.
- **Cada 30 minutos** se leen las noticias. **Cada domingo** se revisa el aprendizaje. **Cada día** se respalda la base de datos.

---

## Instalación (Windows 11)

Requisitos: [Python 3.11 o superior](https://www.python.org/downloads/) (marca *Add python.exe to PATH*) y
[Git](https://git-scm.com/download/win).

```powershell
git clone https://github.com/MC-Cervera/mi-bot-trading.git
cd mi-bot-trading
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1          # si PowerShell lo bloquea: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
copy .env.example .env              # y rellénalo (siguiente sección)
python -m pytest                    # deben pasar todas las pruebas
```

Cada vez que abras una terminal nueva, entra en la carpeta y activa el entorno: `.venv\Scripts\Activate.ps1`.

---

## Configuración: claves y avisos

Las claves van **solo** en el archivo `.env` de tu PC o del VPS. Ese archivo nunca se sube a GitHub (está en
`.gitignore`). **Nunca escribas claves en `.env.example`**: es una plantilla pública, y una prueba automática
falla si alguien lo hace.

| Variable | Para qué | Dónde se obtiene |
|---|---|---|
| `BINANCE_API_KEY` / `BINANCE_API_SECRET` | Saldo y órdenes en **Binance Demo** | demo.binance.com → API Management. **Solo lectura y trading, nunca retiros** |
| `ANTHROPIC_API_KEY` | Claude | console.anthropic.com → API Keys. Pon también un **límite de gasto** en la consola |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Avisos al celular | Ver abajo |
| `PANEL_CLAVE` | Contraseña del panel | La eliges tú (obligatoria en el VPS) |
| `MODO_REAL_AUTORIZADO` | Candado del dinero real | **Déjala vacía** |

Para descargar precios **no hace falta ninguna clave** (son datos públicos). Sin la clave de Anthropic, el bot
funciona pero la cartera con Claude no opera.

**Telegram:**
1. En Telegram busca **@BotFather**, escribe `/newbot` y copia el token en `TELEGRAM_BOT_TOKEN`.
2. Envía cualquier mensaje a tu bot nuevo.
3. Ejecuta `python scripts/telegram_chat_id.py` y copia el número en `TELEGRAM_CHAT_ID`.
4. En `config/config.yaml`, pon `telegram: habilitado: true`.

Todo lo demás (pares, riesgo, estrategia, costos, horarios) se ajusta en `config/config.yaml`, que está comentado línea a línea.

---

## Primeros pasos, en orden

```powershell
python scripts/verificar_conexion.py        # 1. comprueba configuración, conexión y (si hay claves) saldo y permisos
python scripts/descargar_historico.py       # 2. ~2 años de velas de 1 h de los 15 pares (incremental)
python scripts/backtest.py                  # 3. backtest walk-forward de la estrategia técnica → reportes/
python scripts/probar_decision.py --par BTC/USDT   # 4. lo que vería Claude ante la última señal (gratis)
python scripts/bot.py                       # 5. arranca el PAPER TRADING (déjalo corriendo)
streamlit run panel/app.py                  # 6. en otra terminal: el panel en http://localhost:8501
```

Lee el informe del paso 3 antes de seguir. **Si la estrategia técnica no es rentable fuera de muestra, conviene
replantearla antes de invertir tiempo y dinero en lo demás.**

---

## Uso diario

| Comando | Qué hace |
|---|---|
| `python scripts/bot.py` | Arranca el bot (paper trading). `--una-vez` ejecuta un solo ciclo |
| `streamlit run panel/app.py` | Panel web |
| `python scripts/estado.py --diario 3` | Estado en la terminal y diario de las últimas 3 operaciones |
| `python scripts/emergencia.py` | **🚨 Botón de emergencia:** cierra todo y detiene el bot (también está en el panel) |
| `python scripts/reactivar.py` | Reactiva el bot tras una emergencia o una parada por caída máxima (después de revisar) |
| `python scripts/noticias.py` | Lee noticias, las analiza y muestra el impacto real por tema |
| `python scripts/aprendizaje.py listar` | Hipótesis, lecciones y ajustes (`revisar`, `ver H0001`, `revertir <id> "motivo"`) |
| `python scripts/informe_final.py` | **Informe comparativo:** técnico solo vs. técnico + Claude vs. comprar y mantener |
| `python scripts/checklist_real.py` | Checklist obligatorio antes de pensar en dinero real |
| `python scripts/escanear_senales.py --ultimas 2` | Señales que da la estrategia por par y por qué se descartan |
| `python scripts/probar_exness.py` | Valida la cuenta demo de Exness (MT5) y qué pares caben en tus reglas |
| `python scripts/datos_demo.py` | Crea `data/demo.db` con datos **simulados** para conocer el panel |

---

## La estrategia

Se evalúa al **cierre** de cada vela de 1 h y se entra en la apertura de la siguiente (nunca con información del futuro).

| | Largo | Corto |
|---|---|---|
| Tendencia | EMA 9 cruza **por encima** de EMA 21 | EMA 9 cruza **por debajo** de EMA 21 |
| Volumen | ≥ 1.5× el promedio de las 20 velas anteriores | igual |
| RSI (14) | entre 40 y 70 (impulso sin sobrecompra) | entre 30 y 60 (debilidad sin sobreventa) |
| Horario | la entrada debe quedar ≥ 2 h antes del cierre de las 23:00 UTC | igual |
| Stop loss | entrada − 1.5 × ATR(14) | entrada + 1.5 × ATR(14) |
| Objetivo | 2 × la distancia del stop | igual |

- **Mercado:** futuros perpetuos USDⓈ-M de Binance, **apalancamiento fijo 1x** (permite cortos sin multiplicar el riesgo).
- **Pares:** BTC, ETH, BNB, SOL, XRP, ADA, LINK, AVAX, LTC, DOT, TRX, BCH, XLM, ATOM y UNI contra USDT.
- **Tamaño:** primero se decide dónde va el stop y luego cuánto comprar, para que tocar el stop cueste **8 USD**
  incluidas las comisiones (o el 1% del capital si es menor).
- Los cruces que no cumplen todas las condiciones se guardan con su motivo: son el grupo de control del aprendizaje.
- Los indicadores están programados a mano y validados contra los ejemplos de referencia de StockCharts.

**Backtesting (`scripts/backtest.py`):** walk-forward de 6 meses de entrenamiento, 2 meses de prueba y avance de
2 meses. La mejor de 81 combinaciones, todas dentro de tus rangos, se mide solo en meses que el optimizador no vio.
Los supuestos son conservadores:
- comisión de 0.05% por lado y deslizamiento de 0.05%;
- el funding de futuros siempre cuenta como costo;
- si una vela toca el stop y el objetivo, se asume que saltó el stop;
- los huecos de precio se ejecutan al precio de apertura;
- las señales con el stop a menos de 0.2% o a más de 10% del precio no se operan (regla R8, igual que en vivo).

El informe se compara con comprar y mantener.

**Probar otras temporalidades (5m, 15m, 30m):** el bot en vivo usa 1 h. Antes de activar otra temporalidad, se
comprueba en el backtest:

```powershell
python scripts/descargar_historico.py --temporalidad 5m 15m 30m 1h   # 5m tarda más y ocupa unos cientos de MB
python scripts/backtest.py --temporalidad 5m 15m 30m 1h
```

Genera un informe por temporalidad y `reportes/backtest_comparacion_FECHA.md`, con todas en una tabla y un veredicto
para cada una. En temporalidades cortas el stop queda cerca del precio: con 1x la posición no puede crecer lo
suficiente para arriesgar 8 USD, pero las comisiones siguen igual. La tabla lo muestra en "Riesgo medio real por
operación" y "Costos / ganancia bruta".

---

## Reglas de riesgo

Están escritas en `bot/riesgo.py`, **ni Claude ni el aprendizaje pueden cambiarlas**, y cada una tiene pruebas que
demuestran que bloquea. Si una propuesta viola cualquiera, no se abre, y quedan registrados **todos** los motivos.

| Regla | Qué exige |
|---|---|
| R1 | La cartera está activa (no pausada ni detenida) |
| R2 | La pérdida del día no llegó al **3%** |
| R3 | La caída desde el máximo de capital no llegó al **15%** |
| R4 | Faltan al menos 2 h para el cierre de las 23:00 UTC |
| R5 | Como máximo **3** posiciones abiertas |
| R6 | Una sola posición por par |
| R7 | **Stop loss obligatorio**, con stop y objetivo del lado correcto del precio |
| R8 | El stop está entre 0.2% y 10% del precio |
| R9 | Si pasa por Claude: su **confianza es de al menos 0.6** y su respuesta es válida y coherente |
| R10 | La pérdida en el stop es de **8 USD como máximo** (o 1% del capital si es menor; Claude solo puede *reducirla*) y la exposición total no supera el capital (1x) |

- Al **−3% en el día**, la cartera se pausa hasta el día siguiente.
- Al **−15% desde el máximo**, se cierra todo, la cartera se detiene y te avisa. Solo tú la reactivas.
- Cerrar posiciones siempre está permitido.
- Una misma señal nunca se opera dos veces, aunque el bot se reinicie.

---

## Claude y las noticias

**Noticias:** se leen los RSS de CoinDesk, Cointelegraph y Decrypt. Un filtro gratuito por palabras clave descarta
las que no hablan de tus activos ni de temas de mercado (Fed, SEC, ETF, hackeos…). Claude resume las demás y las
clasifica por sentimiento (−1 a 1), impacto (bajo, medio o alto), horizonte y tema.

**Impacto real:** para cada noticia relevante se mide cuánto se movió el precio 1 h, 4 h y 24 h después, y cuánto
de ese movimiento fue propio del activo. Con eso se arma una tabla por tema, por ejemplo: *"¿las noticias de
regulación de impacto alto mueven de verdad el precio? ¿hacia donde decía el sentimiento?"*. Claude recibe ese
historial cuando llega una noticia parecida. Lo ves en el panel, en la sección Noticias.

**Decisión:** a Claude (Sonnet) **solo se le consulta** cuando hay una señal técnica confirmada, o cuando una noticia
de impacto alto afecta a una posición abierta. Recibe la señal, las últimas 24 velas, las noticias que el bot ya
conocía, el impacto histórico, las posiciones, el saldo, las reglas y las lecciones vigentes. Responde en JSON con
un esquema fijo validado con pydantic:

```json
{"accion": "comprar | vender | mantener | cerrar", "par": "BTC/USDT", "confianza": 0.0,
 "tamano_sugerido_pct": 0.0, "stop_loss": 0.0, "take_profit": 0.0, "razonamiento": "...",
 "hipotesis_que_aplica": ["L0001"], "factores_a_favor": ["..."], "factores_en_contra": ["..."]}
```

Límites que Claude no puede saltarse:
- Ante una señal solo puede aprobarla o rechazarla; nunca invertir su dirección.
- Una noticia sola solo puede **cerrar** una posición, nunca abrirla.
- Cualquier error, rechazo, respuesta inválida o presupuesto agotado significa que **no se opera**.

---

## Aprendizaje

El bot separa lo que **sospecha** (hipótesis) de lo que **ha demostrado** (lecciones). Cada domingo a las 23:30 UTC:

1. **Propone** hipótesis comprobables. Las busca un análisis estadístico y Claude sugiere hasta 5. Ambos ven solo
   el tramo de *exploración*: el 60% más antiguo del histórico y el paper trading.
2. **Las prueba** con datos distintos cada vez:
   - *confirmación*, con el 40% reciente del histórico que el buscador no vio;
   - *hacia adelante*, con datos generados **después** de crear la hipótesis.

   Para pasar exige 30 o más operaciones en el grupo y en el control, una diferencia de 0.10R o más, p < 0.05 y que
   el efecto se repita en las dos mitades del periodo.
3. **Crea lecciones** con evidencia (número de operaciones, acierto, R medio frente al control, fecha). Se pasan a
   Claude en cada decisión. Si la lección es de parámetro, se aplica el ajuste **solo dentro de tus rangos**, con el
   valor anterior, la justificación y el historial.
4. **Revalida** con datos nuevos: si el efecto se debilita, la lección queda *en revisión*; si se invierte, se
   **refuta**, se **revierte** el ajuste y se explica por qué.

Espera **pocas lecciones y lentas**: es a propósito. Un bot que "aprende" algo cada semana casi seguro está aprendiendo ruido.

---

## Panel web

`streamlit run panel/app.py` abre **http://localhost:8501**.

| Sección | Contenido |
|---|---|
| Resumen | Capital, posiciones, resultado total y del día, estado de cada cartera, **botón de emergencia**, eventos de riesgo |
| Operaciones | Acierto, factor de beneficio, Sharpe, peor racha y caída máxima; curva de capital y drawdown; resultados diarios, semanales, mensuales y anuales; tabla filtrable |
| Detalle de operación | El diario: señal exacta, qué dijo Claude y el contexto que recibió, noticias, reglas que la permitieron, lecciones aplicadas, salida y análisis posterior |
| Aprendizaje | Hipótesis y "Lo aprendido" con buscador, filtros, evidencia, operaciones relacionadas e historial; cambios de configuración |
| Noticias | Noticias con sentimiento e impacto, e **impacto real medido** por tema |
| Costos | ¿Se paga sola la IA? Aporte de Claude frente a la cartera de control, menos lo que costó |

Para conocerlo sin esperar semanas usa `python scripts/datos_demo.py` y luego
`$env:PANEL_DB="data/demo.db"; streamlit run panel/app.py`. Son datos **simulados** y el panel lo avisa.

---

## Servidor VPS

**Linux (Ubuntu/Debian), recomendado:**

```bash
git clone https://github.com/MC-Cervera/mi-bot-trading.git && cd mi-bot-trading
cp .env.example .env && nano .env       # rellena claves y PANEL_CLAVE
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/descargar_historico.py
bash deploy/linux/instalar.sh           # instala y arranca los servicios mi-bot y mi-bot-panel
```

- Sigue el registro con `journalctl -u mi-bot -f` y `logs/bot.log`. Actualiza con `git pull && sudo systemctl restart mi-bot mi-bot-panel`.
- Si el bot falla, el servicio lo reinicia solo. **Tras una emergencia no se reinicia**: primero revisas, luego ejecutas `scripts/reactivar.py`.
- **El panel no se expone a internet:** escucha solo en 127.0.0.1. Desde tu PC abre un túnel con
  `ssh -L 8501:localhost:8501 usuario@IP-DEL-VPS` y entra en http://localhost:8501.
- La hora del servidor se sincroniza (NTP), porque el cierre diario depende de ella.

**Windows (PC o VPS Windows):**
- `deploy\windows\iniciar_bot.bat` y `deploy\windows\iniciar_panel.bat` arrancan el bot y el panel.
- Para que arranque solo: Programador de tareas → Crear tarea → Desencadenador *Al iniciar el sistema* → Acción:
  `iniciar_bot.bat` → Configuración: *Si la tarea no se ejecuta, reiniciar cada 1 minuto*.
- En un PC, **desactiva la suspensión** (Configuración → Sistema → Energía): si el equipo se duerme, el bot se detiene.

La base de datos se respalda cada día en `data/respaldos/` (se guardan las últimas 14 copias).

### Exness (MetaTrader 5)

La cartera con Claude puede ejecutar sus órdenes en una **cuenta demo de Exness** en lugar de simularlas. Las
señales se siguen calculando con datos de Binance; Exness solo ejecuta (CFDs de cripto). **Solo funciona en Windows**,
porque la librería `MetaTrader5` no existe para Linux; en un VPS hace falta que sea Windows. **El broker está
probado con un MetaTrader simulado, no contra Exness real**, así que primero valídalo tú.

1. Abre una **cuenta demo** en Exness con un saldo parecido al capital simulado (1000 USD) e instala MetaTrader 5.
   Déjalo abierto con esa cuenta.
2. `pip install MetaTrader5` y rellena en `.env` `EXNESS_LOGIN`, `EXNESS_PASSWORD` y `EXNESS_SERVIDOR`
   (y `EXNESS_TERMINAL` si MT5 no está en la ruta por defecto).
3. `python scripts/probar_exness.py`: comprueba que la cuenta es **demo** (si no, se niega a operar), qué pares
   existen y si el **lote mínimo** de cada uno cabe en tus reglas de riesgo.
4. `python scripts/probar_exness.py --orden ETH/USDT`: abre y cierra una posición mínima y verifica que el stop y
   el objetivo quedan en el servidor.
5. Si todo sale OK: `ejecucion: broker: exness_demo` en `config/config.yaml`. Si tus símbolos tienen sufijo (por
   ejemplo `BTCUSDm`), pon `exness: sufijo_simbolo: "m"`, o mapea cada par a mano en `exness.simbolos`.

Cómo funciona:
- El stop y el objetivo viajan con la orden y los ejecuta el servidor de Exness, aunque el bot se caiga.
- El volumen se redondea **hacia abajo** al paso de lote: nunca se arriesga más de lo planificado.
- Cada orden lleva un número mágico y un comentario único, así que no se duplica si hay un reintento.
- Se registran la comisión y el swap reales, sin simular funding encima.

⚠️ **Lote mínimo:** con 1000 USD de capital, apalancamiento 1x y 3 posiciones, cada posición puede valer como
máximo unos 333 USD. Si el lote mínimo de un par vale más que eso (por ejemplo, 0.01 BTC ≈ 600 USD), ese par
**no se puede operar** sin romper tus reglas. El bot lo bloquea y lo registra con el motivo, y `probar_exness.py`
te dice qué pares quedan disponibles.

---

## Paso a dinero real

**Hoy no es posible, y es a propósito.** Hay tres candados:
1. `bot.py` solo arranca en modo paper.
2. `modo: real` exige `MODO_REAL_AUTORIZADO=AUTORIZO_OPERAR_CON_DINERO_REAL` en `.env`.
3. Las reglas de riesgo deben ser coherentes con el capital real.

Antes de plantearlo:

```powershell
python scripts/informe_final.py      # ¿qué funcionó: técnico solo, con Claude o comprar y mantener?
python scripts/checklist_real.py     # todos los puntos automáticos deben estar en ✅
```

El checklist comprueba:
- que pasan las pruebas;
- que el backtest fuera de muestra es rentable y no se detuvo;
- que hay 14 días o más de paper trading y 7 días seguidos sin intervención;
- que el resultado del paper trading es aceptable con 30 operaciones o más;
- que las reglas de riesgo son coherentes con el capital real;
- que Telegram está configurado.

Además lista los puntos manuales: claves sin retiros, límite de gasto en Anthropic, botón de emergencia probado y
diario revisado.

⚠️ **Con 50 USD y un SL de 8 USD, cada pérdida es el 16% de la cuenta.** Eso viola el 1% por operación, y una sola
pérdida supera el drawdown máximo del 15%. El checklist lo marca como falla. Antes de ir a real hay que decidir
entre tener unos 800 USD o más de capital, o bajar el riesgo por operación a unos 0.50 USD. Con posiciones tan
pequeñas, además, las comisiones y los mínimos de orden del exchange pesan mucho.

Cuando todo esté en verde y lo autorices, se hará una fase adicional: activar el broker real (reutiliza el de Binance
Demo, que antes debes validar con `scripts/probar_orden_demo.py`), empezar con el capital mínimo y revisarlo juntos.

---

## Costos

- **Claude (Sonnet):** tope configurable, hoy 15 USD al mes. Al alcanzarlo, el bot deja de consultar a Claude y la
  cartera con Claude no abre operaciones. Cada llamada queda registrada con sus tokens y su costo; el panel compara
  ese gasto con lo que Claude aporta.
  - Estimación, no medición: 0.01–0.03 USD por decisión y 0.02–0.04 USD por lote de noticias.
- **Exchange:** en el backtest se asume 0.05% por lado. Verifica tu nivel real de comisiones en Binance.

---

## Limitaciones honestas

- **No hay garantía de rentabilidad.** El backtest y el paper trading simulan, no predicen.
- Con velas de 1 h no se sabe qué tocó antes dentro de una vela: se asume siempre el peor caso (el stop).
- El impacto de las noticias solo se mide desde que el bot corre: los RSS gratuitos no traen noticias antiguas.
- Claude no se puede evaluar en backtest: conoce el pasado, así que el resultado estaría contaminado. Por eso se
  compara solo en paper trading y en paralelo con la cartera de control.
- El broker de **Binance Demo es experimental**; por defecto las órdenes se simulan dentro del bot.
- **Exness está implementado pero es experimental:** se probó con un MetaTrader 5 simulado y debes validarlo con
  `scripts/probar_exness.py` en una cuenta demo. Algunos pares pueden quedar fuera por el lote mínimo.
- **Bitso no está implementado.** FXIFY y FTMO quedaron fuera.
- Todo el desarrollo se probó con datos simulados, porque el entorno de desarrollo no tenía acceso a Binance ni a
  Anthropic. La primera ejecución real es en tu equipo.

---

## Preguntas frecuentes

**¿Por qué la cartera con Claude no abre nada?** Mira en el panel (Resumen → eventos) el motivo de cada bloqueo.
Las causas habituales son: falta `ANTHROPIC_API_KEY`, presupuesto agotado, Claude rechazó la señal o confianza menor que 0.6.

**El bot se detuvo solo.** Alcanzó el −15% desde el máximo o alguien pulsó emergencia. Revisa el motivo con
`scripts/estado.py` o en el panel antes de usar `scripts/reactivar.py`.

**¿Puedo cambiar la estrategia?** Los parámetros de `estrategia` en `config/config.yaml` sí, siempre dentro de sus
rangos `min`/`max`. Después vuelve a ejecutar el backtest. Las reglas de `riesgo` no se tocan sin pensarlo mucho.

**¿Cuánto tarda en aprender algo?** Una lección necesita 30 operaciones nuevas en cada grupo después de proponerse:
semanas o meses. Es normal que durante el paper trading no aparezca ninguna.

---

## Estructura del proyecto

```
config/config.yaml        configuración comentada (riesgo fijo + parámetros ajustables con rangos)
bot/config.py             carga y validación, candado del dinero real
bot/datos/                conexión al exchange y velas históricas
bot/indicadores.py        EMA, RSI, volumen relativo, ATR
bot/senales.py            reglas de señal y su explicación para el diario
bot/backtest/             motor vela a vela, métricas, comprar y mantener, walk-forward, informe
bot/noticias/             RSS, filtro, análisis con Claude, impacto real
bot/ia/                   cliente de Claude (presupuesto y costos), contexto y decisión
bot/riesgo.py             capa de riesgo (R1–R10)
bot/dimensionamiento.py   tamaño de posición a partir del stop
bot/cartera.py            carteras: aperturas, cierres, stops, límites, emergencia, curva de capital
bot/ciclo.py              ciclo del bot (horario, monitor, noticias)
bot/ejecucion/            brokers: simulado, Binance Demo y Exness MT5 (experimentales)
bot/aprendizaje/          hipótesis, estadística, lecciones, ajustes reversibles, revisión con Claude
bot/diario.py             diario de trading y análisis posterior
bot/informe_final.py      informe comparativo final
bot/checklist.py          checklist antes de pasar a real
panel/                    panel web (Streamlit + Plotly)
scripts/                  comandos
deploy/                   servicios para VPS Linux y lanzadores para Windows
tests/                    pruebas (pytest)
```
