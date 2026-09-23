#!/usr/bin/env bash
# Instala el bot como servicio en un VPS Linux (Ubuntu/Debian). Ejecutar desde la carpeta del proyecto:
#   bash deploy/linux/instalar.sh
set -euo pipefail
RUTA="$(pwd)"
USUARIO="$(whoami)"
[ -f "$RUTA/scripts/bot.py" ] || { echo "Ejecútalo desde la carpeta mi-bot-trading"; exit 1; }
[ -f "$RUTA/.env" ] || { echo "Falta .env: cp .env.example .env y rellénalo (nunca lo subas a GitHub)"; exit 1; }
chmod 600 "$RUTA/.env"
command -v python3.11 >/dev/null || command -v python3 >/dev/null || { echo "Instala Python 3.11+"; exit 1; }
PY=$(command -v python3.11 || command -v python3)
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -m pytest -q
for s in mi-bot mi-bot-panel; do
  sed "s|__USUARIO__|$USUARIO|g; s|__RUTA__|$RUTA|g" "deploy/linux/$s.service" | sudo tee "/etc/systemd/system/$s.service" >/dev/null
done
sudo timedatectl set-ntp true || true   # la hora exacta importa: el cierre diario es a las 23:00 UTC
sudo systemctl daemon-reload
sudo systemctl enable --now mi-bot mi-bot-panel
echo "Listo. Estado:   sudo systemctl status mi-bot"
echo "Registro:        journalctl -u mi-bot -f    (y logs/bot.log)"
echo "Panel:           desde tu PC: ssh -L 8501:localhost:8501 $USUARIO@IP-DEL-VPS  → http://localhost:8501"
