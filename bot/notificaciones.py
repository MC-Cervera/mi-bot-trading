"""Avisos por Telegram. Si no está configurado o falla, solo se registra en el log: nunca detiene el bot."""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)


class Notificador:
    def __init__(self, token: str = "", chat_id: str = "", habilitado: bool = False, timeout: float = 10.0):
        self.token = token
        self.chat_id = chat_id
        self.habilitado = habilitado and bool(token and chat_id)
        self.timeout = timeout
        self.enviados: list[str] = []  # historial en memoria (útil para pruebas y para el panel)

    def enviar(self, texto: str) -> bool:
        self.enviados.append(texto)
        log.info("AVISO: %s", texto.replace("\n", " | "))
        if not self.habilitado:
            return False
        datos = urllib.parse.urlencode({"chat_id": self.chat_id, "text": texto[:4000]}).encode()
        try:
            with urllib.request.urlopen(
                f"https://api.telegram.org/bot{self.token}/sendMessage", data=datos, timeout=self.timeout
            ) as r:
                return json.loads(r.read()).get("ok", False)
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo enviar el aviso por Telegram: %s", e)
            return False
