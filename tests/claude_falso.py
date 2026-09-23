"""Cliente de Anthropic simulado para pruebas: no hace llamadas reales ni gasta dinero."""
from types import SimpleNamespace


def respuesta(parsed, stop_reason="end_turn", entrada=1000, salida=500, cache_lectura=0, cache_escritura=0):
    return SimpleNamespace(
        parsed_output=parsed, stop_reason=stop_reason, stop_details=None, _request_id="req_prueba",
        usage=SimpleNamespace(input_tokens=entrada, output_tokens=salida, cache_read_input_tokens=cache_lectura,
                              cache_creation_input_tokens=cache_escritura),
    )


class ClienteFalso:
    def __init__(self, *respuestas):
        self._respuestas = list(respuestas)
        self.llamadas = []
        self.messages = self

    def parse(self, **kwargs):
        self.llamadas.append(kwargs)
        r = self._respuestas.pop(0)
        if callable(r) and not isinstance(r, SimpleNamespace):
            r = r(kwargs)
        if isinstance(r, Exception):
            raise r
        return r
