"""Ayuda a obtener tu TELEGRAM_CHAT_ID.

1. En Telegram, habla con @BotFather -> /newbot -> copia el token en .env (TELEGRAM_BOT_TOKEN=...).
2. Abre el chat con tu bot nuevo y envíale cualquier mensaje (por ejemplo "hola").
3. Ejecuta:  python scripts/telegram_chat_id.py
4. Copia el número que aparece en .env (TELEGRAM_CHAT_ID=...) y pon `telegram: habilitado: true` en config.yaml.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import cargar_secretos  # noqa: E402
from bot.notificaciones import Notificador  # noqa: E402


def main() -> int:
    s = cargar_secretos()
    if not s.telegram_bot_token:
        print("Falta TELEGRAM_BOT_TOKEN en .env")
        return 1
    with urllib.request.urlopen(f"https://api.telegram.org/bot{s.telegram_bot_token}/getUpdates", timeout=15) as r:
        datos = json.loads(r.read())
    chats = {m["message"]["chat"]["id"]: m["message"]["chat"].get("first_name", "") for m in datos.get("result", [])
             if "message" in m}
    if not chats:
        print("No hay mensajes. Envía un mensaje a tu bot en Telegram y vuelve a ejecutar.")
        return 1
    for cid, nombre in chats.items():
        print(f"TELEGRAM_CHAT_ID={cid}   ({nombre})")
    if s.telegram_chat_id:
        ok = Notificador(s.telegram_bot_token, s.telegram_chat_id, True).enviar("✅ Prueba del bot de trading")
        print("Mensaje de prueba enviado" if ok else "No se pudo enviar el mensaje de prueba")
    return 0


if __name__ == "__main__":
    sys.exit(main())
