"""Panel web del bot (Streamlit).

Arranque:  streamlit run panel/app.py
En el VPS, protégelo con PANEL_CLAVE en .env y accede por un túnel SSH (ver README).
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from bot import proceso  # noqa: E402
from bot.arranque import ARCHIVO_DETENER  # noqa: E402
from bot.config import cargar_config, cargar_secretos  # noqa: E402
from bot.db import crear_motor, crear_sesion  # noqa: E402
from bot.noticias.impacto import estadisticas_impacto  # noqa: E402
from panel import datos, graficos  # noqa: E402

st.set_page_config(page_title="Mi bot de trading", page_icon="📈", layout="wide")

ESTADOS = {"activo": "🟢 Activo", "pausado": "⏸️ Pausado (pérdida diaria)", "detenido": "🛑 Detenido",
           "sin iniciar": "⚪ Sin iniciar"}
ESTADOS_HIP = {"propuesta": "🟡 Propuesta", "en_prueba": "🔵 En prueba", "validada": "✅ Validada",
               "descartada": "⚫ Descartada"}
ESTADOS_LEC = {"vigente": "✅ Vigente", "en_revision": "🟠 En revisión", "refutada": "❌ Refutada",
               "reemplazada": "↪️ Reemplazada"}


# ------------------------------------------------------------------ acceso y conexión
def comprobar_clave() -> None:
    cargar_secretos()  # carga .env en las variables de entorno
    clave = os.getenv("PANEL_CLAVE", "")
    if not clave:
        return
    if st.session_state.get("autenticado"):
        return
    st.title("🔒 Panel del bot")
    intento = st.text_input("Clave del panel", type="password")
    if intento and hmac.compare_digest(intento, clave):
        st.session_state["autenticado"] = True
        st.rerun()
    elif intento:
        st.error("Clave incorrecta")
    st.stop()


@st.cache_resource
def conexion():
    config = cargar_config()
    ruta = os.getenv("PANEL_DB") or config.rutas.absoluta(config.rutas.base_datos)
    return config, crear_motor(ruta)


def sesion():
    _, motor = conexion()
    return crear_sesion(motor)


def modo() -> str:
    try:
        return st.context.theme.type or "light"
    except Exception:  # noqa: BLE001
        return "light"


def usd(x) -> str:
    return "—" if x is None or pd.isna(x) else f"{x:+,.2f} USD"


def ayuda(texto: str) -> None:
    with st.expander("ℹ️ ¿Qué significa esto?"):
        st.markdown(texto)


# ------------------------------------------------------------------ secciones
def texto_duracion(segundos: float) -> str:
    segundos = int(segundos)
    if segundos < 60:
        return f"{segundos} s"
    if segundos < 3600:
        return f"{segundos // 60} min"
    return f"{segundos // 3600} h {segundos % 3600 // 60} min"


def control_bot(en_barra: bool = False) -> None:
    """Estado del proceso del bot y botones para iniciarlo o detenerlo."""
    contenedor = st.sidebar if en_barra else st
    e = proceso.estado()
    if ARCHIVO_DETENER.exists():
        contenedor.error("🛑 Detenido por EMERGENCIA")
        if not en_barra:
            st.write("Revisa qué pasó y reactívalo desde PowerShell con `python scripts/reactivar.py`.")
        return
    if e.en_marcha:
        contenedor.success(f"🟢 Bot en marcha · {texto_duracion(time.time() - (e.iniciado or time.time()))}")
        if e.parada_solicitada:
            contenedor.info("⏳ Deteniéndose (tarda hasta 30 s)...")
        elif contenedor.button("⏹️ Detener bot", key=f"detener_{en_barra}",
                               help="Apaga el bot SIN cerrar las posiciones. Para cerrar todo usa la emergencia."):
            proceso.pedir_parada()
            st.rerun()
    else:
        contenedor.warning("⚪ Bot apagado")
        if contenedor.button("▶️ Iniciar bot", key=f"iniciar_{en_barra}", type="primary",
                             help="Arranca el paper trading. Sigue funcionando aunque cierres el panel."):
            try:
                proceso.iniciar()
                with st.spinner("Arrancando el bot..."):
                    for _ in range(30):
                        time.sleep(1)
                        if proceso.estado().en_marcha:
                            break
                st.rerun()
            except RuntimeError as err:
                contenedor.error(str(err))
        if not en_barra and proceso.ARCHIVO_CONSOLA.exists():
            with st.expander("Últimos mensajes del bot"):
                lineas = proceso.ARCHIVO_CONSOLA.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
                st.code("\n".join(lineas) or "(vacío)")


def tabla_posiciones_abiertas(s) -> None:
    st.subheader("📂 Posiciones abiertas ahora")
    pos = datos.posiciones_abiertas(s)
    if pos.empty:
        st.info("No hay posiciones abiertas en este momento.")
        return
    orden = ["cartera", "par", "direccion", "resultado_usd", "resultado_r", "precio_actual", "stop_loss", "take_profit",
             "dist_sl_pct", "dist_tp_pct", "entrada_fecha", "horas_abierta", "precio_entrada", "nocional"]
    st.dataframe(pos[orden], hide_index=True, width="stretch", column_config={
        "cartera": "Cartera", "par": "Par", "direccion": "Dirección",
        "entrada_fecha": st.column_config.DatetimeColumn("Entrada (UTC)", format="DD/MM HH:mm"),
        "horas_abierta": st.column_config.NumberColumn("Horas abierta", format="%.1f"),
        "precio_entrada": st.column_config.NumberColumn("Precio entrada", format="%.4f"),
        "precio_actual": st.column_config.NumberColumn("Precio actual", format="%.4f"),
        "stop_loss": st.column_config.NumberColumn("Stop loss", format="%.4f"),
        "take_profit": st.column_config.NumberColumn("Take profit", format="%.4f"),
        "resultado_usd": st.column_config.NumberColumn("Resultado (USD)", format="%+.2f"),
        "resultado_r": st.column_config.NumberColumn("Resultado (R)", format="%+.2f",
                                                     help="1 R = lo que se pierde si salta el stop loss (8 USD)"),
        "dist_sl_pct": st.column_config.NumberColumn("Distancia al SL", format="%.2f %%"),
        "dist_tp_pct": st.column_config.NumberColumn("Distancia al TP", format="%.2f %%"),
        "nocional": st.column_config.NumberColumn("Tamaño (USD)", format="%.2f"),
    })
    ayuda("Resultado **sin realizar**: cambia con el precio y aún no descuenta la comisión de salida. El detalle "
          "completo de cada una (por qué se abrió, qué dijo Claude) está en **Detalle**. Todas se cierran a las 23:00 UTC.")


def seccion_resumen(s) -> None:
    st.header("Resumen")
    st.subheader("🤖 Estado del bot")
    control_bot()
    ayuda("**Iniciar** arranca el paper trading: cada hora busca señales, cada minuto vigila los stops y cada 30 min lee "
          "noticias. Sigue funcionando aunque cierres esta página (no si apagas o suspendes el PC). **Detener** lo apaga "
          "sin cerrar posiciones; al volver a iniciarlo, las retoma. Para cerrar TODO usa el botón de emergencia.")
    if not datos.operaciones(s).shape[0]:
        st.info("Todavía no hay operaciones. Con el bot en marcha, las primeras aparecen cuando hay señales (puede tardar "
                "horas o días). La curva de capital se registra cada hora. Para ver cómo se ve el panel con datos, usa "
                "`python scripts/datos_demo.py` (datos simulados).")
    if ARCHIVO_DETENER.exists():
        st.error("🛑 El bot está DETENIDO por el botón de emergencia. Revisa qué pasó y reactívalo con "
                 "`python scripts/reactivar.py`.")
    cols = st.columns(2)
    for col, r in zip(cols, datos.resumen_carteras(s)):
        with col:
            st.subheader(r["nombre"])
            st.markdown(f"**Estado:** {ESTADOS.get(r['estado'], r['estado'])}" + (f" — {r['motivo']}" if r["motivo"] else ""))
            a, b = st.columns(2)
            a.metric("Capital (simulado)", f"{r['capital']:,.2f} USD", f"{r['pnl_total']:+,.2f} USD en total")
            b.metric("Resultado de hoy", usd(r["pnl_hoy"]))
            a.metric("Posiciones abiertas", r["posiciones_abiertas"])
            b.metric("Valor en posiciones", f"{r['valor_posiciones']:,.2f} USD")
    st.caption("Las posiciones abiertas se valoran con el cierre de la última vela guardada (1 h).")
    ayuda("Hay dos carteras simuladas de 1000 USD que reciben **las mismas señales**. Una pasa cada señal por Claude "
          "antes de operar; la otra opera la señal técnica tal cual. Comparándolas se sabe si Claude mejora los "
          "resultados o solo cuesta dinero.")
    tabla_posiciones_abiertas(s)

    st.subheader("🚨 Botón de emergencia")
    st.write("Cierra **todas** las posiciones de las dos carteras y detiene el bot hasta que lo reactives a mano.")
    confirmo = st.checkbox("Entiendo que se cerrará todo y el bot quedará detenido")
    if st.button("CERRAR TODO Y DETENER", type="primary", disabled=not confirmo):
        from bot.emergencia import activar_emergencia
        with st.spinner("Cerrando posiciones..."):
            r = activar_emergencia("Botón de emergencia (panel)")
        if r["error"]:
            st.error(r["error"])
        st.success("Bot detenido. " + "; ".join(f"{c}: sin cerrar {p}" for c, p in r["pendientes"].items() if p))

    st.subheader("Últimos eventos de riesgo")
    ev = datos.eventos(s, 20)
    if ev.empty:
        st.info("Sin eventos todavía.")
    else:
        st.dataframe(ev, hide_index=True, width="stretch",
                     column_config={"fecha": st.column_config.DatetimeColumn("Fecha (UTC)", format="DD/MM HH:mm")})
    ayuda("Aquí aparece cada vez que la capa de riesgo **bloqueó** una operación (y por qué: regla R1 a R10), "
          "cuando Claude rechazó una señal, las pausas por pérdida diaria y las paradas.")


def seccion_operaciones(s) -> None:
    st.header("Operaciones")
    ops = datos.operaciones(s)
    m = modo()
    c1, c2 = st.columns(2)
    for col, cartera in zip((c1, c2), datos.CARTERAS):
        met = datos.metricas(s, cartera)
        with col:
            st.subheader(datos.NOMBRE_CARTERA[cartera])
            a, b = st.columns(2)
            a.metric("Tasa de acierto", f"{met.get('tasa_acierto_pct', 0):.1f}%")
            fb = met.get("factor_beneficio", 0)
            b.metric("Factor de beneficio", "∞" if fb == float("inf") else f"{fb:.2f}")
            a.metric("Sharpe (anual)", f"{met.get('sharpe', 0):.2f}")
            b.metric("Peor racha (pérdidas seguidas)", f"{met.get('peor_racha', 0)}")
            a.metric("Caída máxima", f"{met.get('max_drawdown_pct', 0):.1f}%")
            b.metric("Operaciones cerradas", met.get("operaciones", 0))
    ayuda("- **Tasa de acierto:** % de operaciones que ganaron.\n- **Factor de beneficio:** lo ganado dividido entre "
          "lo perdido. Mayor que 1 = gana más de lo que pierde.\n- **Sharpe:** ganancia en relación con lo que oscila "
          "el capital (más alto = más estable). Con pocas semanas de datos es muy poco fiable.\n- **Peor racha:** "
          "máximo de pérdidas seguidas; sirve para saber qué racha mala esperar.")

    curva = datos.curva_capital(s)
    if curva.empty:
        st.info("Aún no hay curva de capital: se registra cada hora cuando el bot está en marcha.")
    else:
        st.subheader("Curva de capital")
        st.plotly_chart(graficos.curva_capital(curva, m), width="stretch", theme="streamlit")
        st.subheader("Caída desde el máximo (drawdown)")
        st.plotly_chart(graficos.drawdown(curva, m), width="stretch", theme="streamlit")

    st.subheader("Resultados por periodo")
    pestanas = st.tabs(["Diario", "Semanal", "Mensual", "Anual"])
    for pestana, periodo in zip(pestanas, "DWMY"):
        with pestana:
            tabla = datos.resultados_por_periodo(ops, periodo)
            if tabla.empty:
                st.info("Sin operaciones cerradas todavía.")
                continue
            st.plotly_chart(graficos.resultados_periodo(tabla, m), width="stretch", theme="streamlit",
                            key=f"periodo_{periodo}")
            st.dataframe(tabla.assign(cartera=tabla["cartera"].map(datos.NOMBRE_CARTERA)), hide_index=True,
                         width="stretch")

    st.subheader("Todas las operaciones")
    if ops.empty:
        st.info("Sin operaciones todavía.")
        return
    f1, f2, f3 = st.columns(3)
    carteras = f1.multiselect("Cartera", list(datos.CARTERAS), format_func=datos.NOMBRE_CARTERA.get, placeholder="Todas")
    pares = f2.multiselect("Par", sorted(ops["par"].unique()), placeholder="Todos")
    estado = f3.multiselect("Estado", ["abierta", "cerrada"], placeholder="Todos")
    vista = ops
    if carteras:
        vista = vista[vista["cartera"].isin(carteras)]
    if pares:
        vista = vista[vista["par"].isin(pares)]
    if estado:
        vista = vista[vista["estado"].isin(estado)]
    st.dataframe(
        vista[["id", "cartera", "par", "direccion", "estado", "entrada_fecha", "precio_entrada", "salida_fecha",
               "precio_salida", "resultado_usd", "r_multiple", "comisiones_usd", "motivo_salida"]],
        hide_index=True, width="stretch",
        column_config={
            "entrada_fecha": st.column_config.DatetimeColumn("Entrada (UTC)", format="DD/MM/YY HH:mm"),
            "salida_fecha": st.column_config.DatetimeColumn("Salida (UTC)", format="DD/MM/YY HH:mm"),
            "resultado_usd": st.column_config.NumberColumn("Resultado (USD)", format="%+.2f"),
            "r_multiple": st.column_config.NumberColumn("R", format="%+.2f",
                                                        help="Resultado en múltiplos del riesgo: -1 = perdió lo planificado"),
            "comisiones_usd": st.column_config.NumberColumn("Comisión (USD)", format="%.2f"),
        })
    st.caption("Para ver por qué se abrió una operación, usa la sección «Detalle de operación».")


def seccion_detalle(s) -> None:
    st.header("Detalle de operación")
    ops = datos.operaciones(s)
    if ops.empty:
        st.info("Sin operaciones todavía.")
        return
    etiquetas = {int(r.id): f"#{r.id} · {datos.NOMBRE_CARTERA[r.cartera]} · {r.direccion} {r.par} · "
                 f"{r.entrada_fecha:%d/%m %H:%M} · {usd(r.resultado_usd) if r.estado == 'cerrada' else 'abierta'}"
                 for r in ops.itertuples()}
    op_id = st.selectbox("Operación", list(etiquetas), format_func=etiquetas.get)
    d = datos.detalle_operacion(s, op_id)
    op = d["operacion"]
    a, b, c, e = st.columns(4)
    a.metric("Resultado", usd(op.pnl_neto) if op.estado == "cerrada" else "abierta")
    b.metric("R", f"{op.r_multiple:+.2f}" if op.r_multiple is not None else "—")
    c.metric("Sugerida por", "Con Claude" if "claude" in op.sugerida_por else "Sin Claude")
    e.metric("Confianza de Claude", f"{op.confianza_claude:.2f}" if op.confianza_claude is not None else "—")

    st.markdown(op.diario_entrada or "")
    if op.diario_salida:
        st.markdown(op.diario_salida)
    if op.analisis_post:
        st.markdown(op.analisis_post)

    with st.expander("📐 Señal técnica (datos exactos)"):
        if d["senal"] is not None:
            sn = d["senal"]
            st.json({"par": sn.par, "vela_utc": str(datos.fecha(sn.ts)), "direccion": sn.direccion, "precio": sn.precio,
                     "ema_rapida": sn.ema_rapida, "ema_lenta": sn.ema_lenta, "volumen_relativo": sn.vol_rel,
                     "rsi": sn.rsi, "atr": sn.atr, "stop_loss": sn.stop_loss, "take_profit": sn.take_profit,
                     "parametros": json.loads(sn.parametros)})
        else:
            st.write("Sin señal registrada.")
    with st.expander("🤖 Razonamiento completo de Claude"):
        if d["decision"]:
            st.json(d["decision"])
            ll = d["llamada"]
            st.caption(f"Costo de esta consulta: {ll.costo_usd:.4f} USD · {ll.tokens_entrada} tokens de entrada, "
                       f"{ll.tokens_salida} de salida, {ll.tokens_cache_lectura} leídos de caché · modelo {ll.modelo}")
            st.text_area("Contexto exacto que recibió Claude", ll.contexto, height=300)
        else:
            st.write("Esta operación no pasó por Claude (cartera de control).")
    with st.expander("🛡️ Reglas de riesgo que la permitieron"):
        for r in d["reglas"]:
            st.markdown(f"- {r}")
    with st.expander("🧠 Lecciones aplicadas"):
        if d["lecciones"]:
            for l in d["lecciones"]:
                st.markdown(f"- **{l['codigo']}** ({ESTADOS_LEC.get(l['estado'], l['estado'])}): {l['enunciado']}")
        else:
            st.write("Ninguna.")


def seccion_aprendizaje(s) -> None:
    st.header("Aprendizaje")
    ayuda("El bot separa lo que **sospecha** (hipótesis) de lo que **ha demostrado** (lecciones). Una hipótesis se "
          "busca en datos antiguos, se confirma con datos que el buscador no vio y, al final, con datos nuevos "
          "generados después de crearla. Solo si pasa las tres etapas se convierte en lección. Si más adelante deja "
          "de cumplirse, se refuta y se deshace cualquier cambio que haya provocado.")
    texto = st.text_input("🔎 Buscar", placeholder="volumen, BTC, horario...")
    t_hip, t_lec, t_aj = st.tabs(["Hipótesis", "Lo aprendido", "Cambios de configuración"])
    with t_hip:
        c1, c2 = st.columns(2)
        estados = c1.multiselect("Estado", list(ESTADOS_HIP), format_func=ESTADOS_HIP.get, placeholder="Todos")
        origenes = c2.multiselect("Origen", ["estadistico", "claude"],
                                  format_func={"estadistico": "Análisis estadístico", "claude": "Sugerencia de Claude"}.get,
                                  placeholder="Todos")
        hs = datos.hipotesis(s, texto, estados, origenes)
        if not hs:
            st.info("Sin hipótesis todavía. Se generan cada domingo (o con `python scripts/aprendizaje.py revisar`).")
        for h in hs:
            with st.expander(f"{ESTADOS_HIP.get(h.estado, h.estado)} · {h.codigo} · {h.enunciado}"):
                st.markdown(f"**Origen:** {'análisis estadístico' if h.origen == 'estadistico' else 'sugerencia de Claude'} · "
                            f"**creada:** {datos.fecha(h.creada_ms):%d/%m/%Y} · **métrica:** {h.metrica} · "
                            f"**muestra mínima:** {h.muestra_minima} operaciones")
                st.markdown(h.explicacion)
                if h.motivo_estado:
                    st.markdown(f"**Estado actual:** {h.motivo_estado}")
                for titulo, ev in (("Confirmación en histórico no usado", h.evidencia_backtest),
                                   ("Prueba con datos nuevos", h.evidencia_adelante)):
                    texto_ev = datos.evidencia_legible(ev)
                    if texto_ev:
                        st.markdown(f"**{titulo}:** {texto_ev}")
                rel = datos.operaciones_relacionadas(s, h)
                if not rel.empty:
                    st.markdown(f"**Operaciones del paper trading que cumplen la condición:** {len(rel)}")
                    st.dataframe(rel.head(50), hide_index=True, width="stretch")
                hist = datos.historial(s, "hipotesis", h.id)
                if not hist.empty:
                    st.markdown("**Historial**")
                    st.dataframe(hist, hide_index=True, width="stretch")
    with t_lec:
        estados = st.multiselect("Estado de la lección", list(ESTADOS_LEC), format_func=ESTADOS_LEC.get,
                                 placeholder="Todos")
        ls = datos.lecciones(s, texto, estados)
        if not ls:
            st.info("Todavía no hay lecciones. Es normal: una hipótesis necesita al menos 30 operaciones nuevas en cada "
                    "grupo para validarse, lo que puede tardar semanas o meses.")
        for l in ls:
            with st.expander(f"{ESTADOS_LEC.get(l.estado, l.estado)} · {l.codigo} · {l.enunciado}"):
                ev = json.loads(l.evidencia)
                ad = ev.get("adelante") or {}
                a, b, c, d = st.columns(4)
                a.metric("Operaciones (grupo)", ad.get("n_grupo", "—"))
                b.metric("Acierto grupo vs control", f"{ad.get('acierto_grupo_pct', 0):.0f}% / {ad.get('acierto_control_pct', 0):.0f}%")
                c.metric("R medio grupo vs control", f"{ad.get('r_medio_grupo', 0):+.2f} / {ad.get('r_medio_control', 0):+.2f}")
                d.metric("Validada", f"{datos.fecha(l.validada_ms):%d/%m/%Y}")
                st.markdown(l.explicacion)
                if l.motivo_estado:
                    st.markdown(f"**Estado:** {l.motivo_estado}")
                hist = datos.historial(s, "leccion", l.id)
                if not hist.empty:
                    st.dataframe(hist, hide_index=True, width="stretch")
    with t_aj:
        aj = datos.ajustes(s)
        if aj.empty:
            st.info("El aprendizaje no ha cambiado ningún parámetro. Solo puede hacerlo dentro de tus rangos y siempre "
                    "de forma reversible (`python scripts/aprendizaje.py revertir <id> \"motivo\"`).")
        else:
            st.dataframe(aj, hide_index=True, width="stretch")


def seccion_noticias(s) -> None:
    st.header("Noticias")
    horas = st.select_slider("Periodo", options=[24, 72, 168, 720], value=72,
                             format_func=lambda h: {24: "24 h", 72: "3 días", 168: "7 días", 720: "30 días"}[h])
    df = datos.noticias(s, horas)
    if df.empty:
        st.info("Sin noticias relevantes en el periodo. Se leen cada 30 minutos cuando el bot está en marcha.")
    else:
        impacto = st.multiselect("Impacto", ["alto", "medio", "bajo"], placeholder="Todos")
        if impacto:
            df = df[df["impacto"].isin(impacto)]
        st.dataframe(df, hide_index=True, width="stretch", column_config={
            "fecha": st.column_config.DatetimeColumn("Fecha (UTC)", format="DD/MM HH:mm"),
            "sentimiento": st.column_config.ProgressColumn("Sentimiento", min_value=-1, max_value=1, format="%+.2f"),
            "url": st.column_config.LinkColumn("Enlace", display_text="abrir")})
    st.subheader("Impacto REAL de las noticias en el precio")
    ayuda("Para cada noticia relevante se mide cuánto se movió el precio 1 h, 4 h y 24 h después, y si fue hacia "
          "donde indicaba su sentimiento. Así se sabe qué tipo de noticias mueven de verdad el mercado. Claude recibe "
          "este historial cuando llega una noticia parecida.")
    stats = estadisticas_impacto(s)
    if stats.empty:
        st.info("Aún no hay datos: hacen falta noticias analizadas y al menos 1 h de precios posteriores.")
    else:
        st.plotly_chart(graficos.impacto_por_tema(stats, modo()), width="stretch", theme="streamlit")
        st.dataframe(stats.round(2), hide_index=True, width="stretch")


def seccion_costos(s) -> None:
    st.header("Costos: ¿se paga sola la IA?")
    v = datos.valor_de_claude(s)
    config, _ = conexion()
    a, b, c, d = st.columns(4)
    a.metric("Resultado técnico + Claude", usd(v["resultado_claude"]))
    b.metric("Resultado técnico solo", usd(v["resultado_solo"]))
    c.metric("Gasto total en Claude", f"{v['gasto_claude']:.2f} USD")
    d.metric("Aporte neto de Claude", usd(v["aporte_neto"]),
             help="(resultado con Claude − resultado sin Claude) − gasto en la API")
    if v["aporte_neto"] > 0:
        st.success("Por ahora Claude aporta más de lo que cuesta. Con pocas operaciones puede ser suerte.")
    elif v["gasto_claude"] > 0 or v["resultado_claude"] or v["resultado_solo"]:
        st.warning("Por ahora Claude NO se paga solo: la cartera sin IA rinde igual o mejor una vez descontado su costo.")
    serie = datos.serie_valor_claude(s)
    if not serie.empty:
        st.plotly_chart(graficos.valor_claude(serie, modo()), width="stretch", theme="streamlit")
        st.dataframe(serie[["dia", "diferencia_acumulada", "gasto_acumulado", "neto_acumulado"]], hide_index=True,
                     width="stretch", column_config={
                         "dia": st.column_config.DatetimeColumn("Día", format="DD/MM/YYYY"),
                         "diferencia_acumulada": st.column_config.NumberColumn("Aporte acumulado (USD)", format="%+.2f"),
                         "gasto_acumulado": st.column_config.NumberColumn("Costo acumulado (USD)", format="%.2f"),
                         "neto_acumulado": st.column_config.NumberColumn("Neto acumulado (USD)", format="%+.2f")})
    costos = datos.costos_claude(s)
    if not costos.empty:
        mes = costos[costos["fecha"] >= pd.Timestamp.now(tz="UTC").normalize().replace(day=1)]
        st.metric("Gasto de este mes", f"{mes['costo_usd'].sum():.2f} / {config.claude.presupuesto_mensual_usd:.0f} USD")
        st.progress(min(1.0, mes["costo_usd"].sum() / max(config.claude.presupuesto_mensual_usd, 0.01)))
        st.dataframe(costos.groupby(["proposito", "estado"])["costo_usd"].agg(["count", "sum"])
                     .rename(columns={"count": "llamadas", "sum": "USD"}).reset_index(), hide_index=True)
    ayuda("Cada consulta a Claude cuesta dinero. Este apartado compara lo que la cartera con Claude ganó (o perdió) "
          "**de más** frente a la cartera de control con lo que se pagó a la API. Si el neto es negativo durante mucho "
          "tiempo, Claude no está aportando valor y conviene reconsiderar usarlo.")


SECCIONES = {"Resumen": seccion_resumen, "Operaciones": seccion_operaciones, "Detalle de operación": seccion_detalle,
             "Aprendizaje": seccion_aprendizaje, "Noticias": seccion_noticias, "Costos": seccion_costos}


def main() -> None:
    comprobar_clave()
    _, motor = conexion()
    if "demo" in str(motor.url):
        st.warning("⚠️ **DATOS DE DEMOSTRACIÓN**: precios sintéticos y un Claude simulado. Estos resultados no dicen "
                   "nada de la estrategia real. Para ver tus datos reales abre el panel sin `PANEL_DB`.")
    st.sidebar.title("📈 Mi bot de trading")
    st.sidebar.caption("PAPER TRADING · dinero simulado")
    control_bot(en_barra=True)
    eleccion = st.sidebar.radio("Sección", list(SECCIONES))
    if st.sidebar.button("🔄 Actualizar"):
        st.rerun()
    s = sesion()
    try:
        SECCIONES[eleccion](s)
    finally:
        s.close()


main()
