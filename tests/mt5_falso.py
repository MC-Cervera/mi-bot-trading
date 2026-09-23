"""MetaTrader 5 simulado para pruebas. Constantes copiadas del paquete oficial MetaTrader5 5.0.6180."""
from types import SimpleNamespace

TRADE_ACTION_DEAL = 1
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
ORDER_TIME_GTC = 0
DEAL_ENTRY_IN, DEAL_ENTRY_OUT = 0, 1
DEAL_REASON_EXPERT, DEAL_REASON_SL, DEAL_REASON_TP = 3, 4, 5
ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_REAL = 0, 2
SYMBOL_TRADE_MODE_FULL = 4
TRADE_RETCODE_REQUOTE, TRADE_RETCODE_DONE = 10004, 10009
TRADE_RETCODE_PRICE_OFF, TRADE_RETCODE_INVALID_FILL = 10021, 10030


class MT5Falso:
    def __init__(self, cuenta_demo=True, filling_mode=2, rechazar_relleno=(), comision_por_lote=-0.5):
        for k, v in globals().items():
            if k.isupper():
                setattr(self, k, v)
        self.cuenta = SimpleNamespace(trade_mode=ACCOUNT_TRADE_MODE_DEMO if cuenta_demo else ACCOUNT_TRADE_MODE_REAL,
                                      balance=1000.0, login=1, server="Exness-MT5Trial")
        self.simbolos = {
            "BTCUSD": dict(bid=60000.0, ask=60010.0, trade_contract_size=1.0, volume_min=0.01, volume_step=0.01,
                           volume_max=100.0, digits=2, point=0.01, trade_stops_level=0),
            "ETHUSD": dict(bid=3000.0, ask=3000.5, trade_contract_size=1.0, volume_min=0.1, volume_step=0.1,
                           volume_max=1000.0, digits=2, point=0.01, trade_stops_level=100),
        }
        self.filling_mode = filling_mode
        self.rechazar_relleno = set(rechazar_relleno)
        self.comision_por_lote = comision_por_lote
        self.posiciones = {}
        self.deals = []
        self.peticiones = []
        self._ticket = 1000

    # -- conexión y cuenta
    def initialize(self, **kw):
        self.init_kwargs = kw
        return True

    def last_error(self):
        return (0, "ok")

    def account_info(self):
        return self.cuenta

    # -- símbolos
    def symbol_select(self, sym, activar):
        return sym in self.simbolos

    def symbol_info(self, sym):
        d = self.simbolos.get(sym)
        return None if d is None else SimpleNamespace(name=sym, trade_mode=SYMBOL_TRADE_MODE_FULL,
                                                      filling_mode=self.filling_mode, **{k: v for k, v in d.items()
                                                                                        if k not in ("bid", "ask")})

    def symbol_info_tick(self, sym):
        d = self.simbolos.get(sym)
        return None if d is None else SimpleNamespace(bid=d["bid"], ask=d["ask"], last=d["bid"])

    # -- órdenes
    def _deal(self, ticket_pos, sym, volumen, precio, entrada, razon):
        self.deals.append(SimpleNamespace(position_id=ticket_pos, symbol=sym, volume=volumen, price=precio, entry=entrada,
                                          reason=razon, commission=self.comision_por_lote * volumen, swap=0.0, fee=0.0))

    def order_send(self, p):
        self.peticiones.append(dict(p))
        if p["type_filling"] in self.rechazar_relleno:
            return SimpleNamespace(retcode=TRADE_RETCODE_INVALID_FILL, comment="Unsupported filling mode",
                                   order=0, deal=0, price=0.0, volume=0.0)
        self._ticket += 1
        if "position" in p:  # cierre
            pos = self.posiciones.pop(p["position"])
            self._deal(pos.ticket, pos.symbol, pos.volume, p["price"], DEAL_ENTRY_OUT, DEAL_REASON_EXPERT)
            return SimpleNamespace(retcode=TRADE_RETCODE_DONE, comment="done", order=self._ticket, deal=self._ticket,
                                   price=p["price"], volume=pos.volume)
        pos = SimpleNamespace(ticket=self._ticket, symbol=p["symbol"], volume=p["volume"], price_open=p["price"],
                              sl=p["sl"], tp=p["tp"], magic=p["magic"], comment=p["comment"],
                              type=POSITION_TYPE_BUY if p["type"] == ORDER_TYPE_BUY else POSITION_TYPE_SELL)
        self.posiciones[pos.ticket] = pos
        self._deal(pos.ticket, pos.symbol, pos.volume, pos.price_open, DEAL_ENTRY_IN, DEAL_REASON_EXPERT)
        return SimpleNamespace(retcode=TRADE_RETCODE_DONE, comment="done", order=self._ticket, deal=self._ticket,
                               price=p["price"], volume=p["volume"])

    def positions_get(self, symbol=None, ticket=None):
        ps = list(self.posiciones.values())
        if ticket is not None:
            ps = [p for p in ps if p.ticket == ticket]
        if symbol is not None:
            ps = [p for p in ps if p.symbol == symbol]
        return tuple(ps)

    def history_deals_get(self, position=None):
        return tuple(d for d in self.deals if d.position_id == position)

    # -- ayuda para pruebas: el servidor ejecuta el stop o el objetivo
    def saltar(self, ticket, cual="sl"):
        pos = self.posiciones.pop(ticket)
        precio = pos.sl if cual == "sl" else pos.tp
        self._deal(pos.ticket, pos.symbol, pos.volume, precio, DEAL_ENTRY_OUT,
                   DEAL_REASON_SL if cual == "sl" else DEAL_REASON_TP)
