"""Bot de trading de criptomonedas con análisis técnico, noticias y Claude."""
import sys

# En Windows la consola puede no ser UTF-8: sin esto, acentos o símbolos (✅, ≥) harían fallar los scripts.
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
