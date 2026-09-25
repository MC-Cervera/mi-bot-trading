"""Verifica configuración, conexión pública, claves (demo), saldo y permisos. Solo lectura: no envía órdenes.

Uso:  python scripts/verificar_conexion.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import ErrorConfiguracion, cargar_config, cargar_secretos, validar_seguridad  # noqa: E402
from bot.datos.exchange import (  # noqa: E402
    crear_cliente_cuenta, crear_cliente_datos, leer_saldo, simbolo_mercado, validar_pares, verificar_permisos_claves,
)
from bot.logging_setup import configurar_logging  # noqa: E402


def main() -> int:
    config = cargar_config()
    configurar_logging(config.rutas.absoluta(config.rutas.logs))
    secretos = cargar_secretos()
    ok = True

    print(f"Modo: {config.modo} | Exchange: {config.exchange.nombre} {config.exchange.tipo_mercado} | demo={config.exchange.demo}")
    try:
        validar_seguridad(config, secretos)
        print("[OK] Configuración segura")
    except ErrorConfiguracion as e:
        print(f"[BLOQUEADO] {e}")
        return 2

    datos = crear_cliente_datos(config)
    try:
        validos, invalidos = validar_pares(datos, config.pares, config.exchange.mercado_datos)
        print(f"[OK] Datos públicos: {len(validos)} pares disponibles" + (f", NO disponibles: {invalidos}" if invalidos else ""))
        ticker = datos.fetch_ticker(simbolo_mercado(validos[0], config.exchange.mercado_datos))
        print(f"     Último precio {validos[0]}: {ticker['last']}")
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] No se pudo conectar a los datos públicos: {type(e).__name__}: {e}")
        ok = False

    if not secretos.tiene_claves_exchange:
        print("[AVISO] Sin claves en .env: se omite la verificación de saldo (no hace falta para descargar histórico)")
        return 0 if ok else 1
    try:
        cuenta = crear_cliente_cuenta(config, secretos)
        s = leer_saldo(cuenta)
        print(f"[OK] Saldo {s['moneda']}: total={s['total']:.2f} libre={s['libre']:.2f} usado={s['usado']:.2f}")
        p = verificar_permisos_claves(cuenta)
        print(f"[{'OK' if p['retiros_habilitados'] is False else 'AVISO'}] Permisos: {p['detalle']}")
        if p["retiros_habilitados"]:
            ok = False
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] Cuenta: {type(e).__name__}: {e}")
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
