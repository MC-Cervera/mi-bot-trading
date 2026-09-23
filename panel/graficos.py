"""Gráficas del panel (Plotly). Colores validados con el validador de paleta en modo claro y oscuro.

- Cada cartera tiene SIEMPRE el mismo color (azul = técnico + Claude, naranja = técnico solo), nunca por posición.
- Una sola escala vertical por gráfica: medidas distintas van en gráficas separadas.
- Líneas de 2 px, barras con esquinas redondeadas y separación, rejilla discreta, leyenda + etiqueta directa.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from bot.cartera import CARTERA_CLAUDE, CARTERA_SOLO

PALETA = {
    "light": {"serie": ["#2a78d6", "#eb6834", "#1baf7a"], "superficie": "#fcfcfb", "texto": "#0b0b0b",
              "texto2": "#52514e", "tenue": "#898781", "rejilla": "#e1e0d9", "base": "#c3c2b7"},
    "dark": {"serie": ["#3987e5", "#d95926", "#199e70"], "superficie": "#1a1a19", "texto": "#ffffff",
             "texto2": "#c3c2b7", "tenue": "#898781", "rejilla": "#2c2c2a", "base": "#383835"},
}
ORDEN_CARTERAS = {CARTERA_CLAUDE: 0, CARTERA_SOLO: 1}
NOMBRES = {CARTERA_CLAUDE: "Técnico + Claude", CARTERA_SOLO: "Técnico solo"}


def _p(modo: str) -> dict:
    return PALETA["dark" if modo == "dark" else "light"]


def color_cartera(cartera: str, modo: str) -> str:
    return _p(modo)["serie"][ORDEN_CARTERAS.get(cartera, 2)]


def _estilo(fig: go.Figure, modo: str, titulo_y: str, alto: int = 340) -> go.Figure:
    p = _p(modo)
    fig.update_layout(
        height=alto, margin=dict(l=8, r=110, t=16, b=8), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=p["texto2"], size=13), hoverlabel=dict(bgcolor=p["superficie"], font_color=p["texto"]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(color=p["texto2"])),
    )
    fig.update_xaxes(showgrid=False, linecolor=p["base"], tickfont=dict(color=p["tenue"]))
    fig.update_yaxes(title=titulo_y, gridcolor=p["rejilla"], zeroline=True, zerolinecolor=p["base"],
                     tickfont=dict(color=p["tenue"]), title_font=dict(color=p["tenue"]))
    return fig


def _etiquetas_finales(fig: go.Figure, etiquetas: list[tuple], color_texto: str, alto_px: int = 300) -> None:
    """Etiqueta directa al final de cada línea; si dos quedan muy cerca se separan verticalmente."""
    if not etiquetas:
        return
    ys = [e[1] for e in etiquetas]
    rango = (max(ys) - min(ys)) or 1.0
    px_por_unidad = alto_px * 0.7 / rango
    ordenadas = sorted(etiquetas, key=lambda e: e[1])
    ultimo_px = None
    for x, y, texto in ordenadas:
        pos = y * px_por_unidad
        desplazamiento = 0.0
        if ultimo_px is not None and pos - ultimo_px < 16:
            desplazamiento = 16 - (pos - ultimo_px)
            pos = ultimo_px + 16
        ultimo_px = pos
        fig.add_annotation(x=x, y=y, text=texto, showarrow=False, xanchor="left", xshift=8, yshift=desplazamiento,
                           font=dict(color=color_texto, size=12))


def curva_capital(df: pd.DataFrame, modo: str) -> go.Figure:
    fig = go.Figure()
    p = _p(modo)
    etiquetas = []
    for cartera in (CARTERA_CLAUDE, CARTERA_SOLO):
        d = df[df["cartera"] == cartera]
        if d.empty:
            continue
        fig.add_trace(go.Scatter(
            x=d["fecha"], y=d["capital"], mode="lines", name=NOMBRES[cartera],
            line=dict(color=color_cartera(cartera, modo), width=2),
            hovertemplate="%{x|%d %b %H:%M} UTC<br>%{fullData.name}: <b>%{y:,.2f} USD</b><extra></extra>"))
        etiquetas.append((d["fecha"].iloc[-1], d["capital"].iloc[-1], NOMBRES[cartera]))
    _etiquetas_finales(fig, etiquetas, p["texto2"], 340)
    fig.update_layout(hovermode="x unified")
    return _estilo(fig, modo, "Capital (USD)")


def drawdown(df: pd.DataFrame, modo: str) -> go.Figure:
    fig = go.Figure()
    for cartera in (CARTERA_CLAUDE, CARTERA_SOLO):
        d = df[df["cartera"] == cartera]
        if d.empty:
            continue
        fig.add_trace(go.Scatter(
            x=d["fecha"], y=d["drawdown_pct"], mode="lines", name=NOMBRES[cartera],
            line=dict(color=color_cartera(cartera, modo), width=2),
            hovertemplate="%{x|%d %b %H:%M} UTC<br>%{fullData.name}: <b>%{y:.2f}%</b> desde el máximo<extra></extra>"))
    fig.add_hline(y=-15, line=dict(color=_p(modo)["tenue"], width=1, dash="dot"),
                  annotation_text="Límite −15% (el bot se detiene)", annotation_position="bottom left",
                  annotation_font_color=_p(modo)["texto2"])
    fig.update_layout(hovermode="x unified")
    return _estilo(fig, modo, "Caída desde el máximo (%)", alto=260)


def resultados_periodo(df: pd.DataFrame, modo: str) -> go.Figure:
    fig = go.Figure()
    for cartera in (CARTERA_CLAUDE, CARTERA_SOLO):
        d = df[df["cartera"] == cartera]
        if d.empty:
            continue
        fig.add_trace(go.Bar(
            x=d["periodo"], y=d["resultado_usd"], name=NOMBRES[cartera], marker_color=color_cartera(cartera, modo),
            marker_cornerradius=4, customdata=d[["operaciones", "acierto_pct"]],
            hovertemplate=("%{x|%d %b %Y}<br>%{fullData.name}: <b>%{y:+,.2f} USD</b><br>"
                           "%{customdata[0]} operaciones · acierto %{customdata[1]:.0f}%<extra></extra>")))
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08)
    return _estilo(fig, modo, "Resultado (USD)", alto=300)


def valor_claude(df: pd.DataFrame, modo: str) -> go.Figure:
    fig = go.Figure()
    p = _p(modo)
    series = [("diferencia_acumulada", "Lo que Claude aportó (vs. control)", "Aporte", 0, "solid"),
              ("gasto_acumulado", "Lo que costó Claude (API)", "Costo", 1, "solid"),
              ("neto_acumulado", "Neto: aporte − costo", "Neto", 2, "dash")]
    etiquetas = []
    for col, nombre, corto, i, trazo in series:
        fig.add_trace(go.Scatter(x=df["dia"], y=df[col], mode="lines+markers", name=nombre,
                                 line=dict(color=p["serie"][i], width=2, dash=trazo), marker=dict(size=8),
                                 hovertemplate="%{x|%d %b %Y}<br>%{fullData.name}: <b>%{y:+,.2f} USD</b><extra></extra>"))
        if len(df):
            etiquetas.append((df["dia"].iloc[-1], df[col].iloc[-1], corto))
    _etiquetas_finales(fig, etiquetas, p["texto2"], 340)
    fig.update_layout(hovermode="x unified")
    return _estilo(fig, modo, "USD acumulados")


def impacto_por_tema(stats: pd.DataFrame, modo: str) -> go.Figure:
    d = stats.sort_values("mov_abs_medio_4h_pct")
    etiquetas = d["tema"].str.replace("_", " ") + " · " + d["impacto"]
    fig = go.Figure(go.Bar(
        x=d["mov_abs_medio_4h_pct"], y=etiquetas, orientation="h", marker_color=_p(modo)["serie"][0],
        marker_cornerradius=4, customdata=d[["muestras", "acierto_direccion_4h_pct"]],
        hovertemplate=("%{y}<br>Movimiento medio a 4 h: <b>%{x:.2f}%</b><br>%{customdata[0]} noticias · "
                       "dirección acertada %{customdata[1]:.0f}%<extra></extra>")))
    fig.update_layout(bargap=0.35, showlegend=False)
    fig = _estilo(fig, modo, "", alto=max(220, 34 * len(d) + 60))
    fig.update_xaxes(title="Movimiento medio del precio 4 h después (%)", title_font=dict(color=_p(modo)["tenue"]),
                     showgrid=True, gridcolor=_p(modo)["rejilla"])
    fig.update_yaxes(showgrid=False)
    return fig
