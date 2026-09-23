"""Descarga de noticias desde fuentes RSS configurables."""
from __future__ import annotations

import calendar
import hashlib
import html
import logging
import re
import urllib.request
from dataclasses import dataclass

import feedparser

log = logging.getLogger(__name__)
AGENTE = "Mozilla/5.0 (mi-bot-trading; lector RSS)"


@dataclass
class NoticiaCruda:
    fuente: str
    titulo: str
    resumen: str
    url: str
    publicada_ms: int

    @property
    def huella(self) -> str:
        return hashlib.sha256(self.url.strip().encode("utf-8")).hexdigest()


def limpiar_html(texto: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", texto or ""))).strip()


def parsear_rss(contenido: bytes | str, fuente: str, ahora_ms: int) -> list[NoticiaCruda]:
    feed = feedparser.parse(contenido)
    noticias = []
    for e in feed.entries:
        url = e.get("link") or ""
        titulo = limpiar_html(e.get("title", ""))
        if not url or not titulo:
            continue
        fecha = e.get("published_parsed") or e.get("updated_parsed")
        publicada = calendar.timegm(fecha) * 1000 if fecha else ahora_ms
        noticias.append(NoticiaCruda(
            fuente=fuente, titulo=titulo, resumen=limpiar_html(e.get("summary", ""))[:1500], url=url,
            publicada_ms=min(publicada, ahora_ms),  # fechas futuras (relojes mal puestos) no se aceptan
        ))
    return noticias


def descargar_fuente(url: str, ahora_ms: int, timeout: float = 20.0) -> list[NoticiaCruda]:
    """Devuelve [] si la fuente falla: una fuente caída no debe detener el bot."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": AGENTE})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            contenido = r.read()
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo leer la fuente %s: %s", url, e)
        return []
    fuente = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return parsear_rss(contenido, fuente, ahora_ms)
