"""Overseas stock order execution helpers."""
from __future__ import annotations

from dataclasses import dataclass

from config.settings import kis_config
from kis.client import KISClient
from order.executor import OrderSide


@dataclass(frozen=True)
class OverseasOrderRequest:
    symbol: str
    exchange: str
    side: OrderSide
    quantity: int
    limit_price: float
    order_type: str = "00"


class OverseasOrderExecutor:
    _TR = {
        "real": {"BUY": "JTTT1002U", "SELL": "JTTT1006U"},
        "sim": {"BUY": "VTTT1002U", "SELL": "VTTT1001U"},
    }

    def __init__(self, client: KISClient):
        self.client = client
        self._mode = "real" if kis_config.is_real else "sim"

    def send(self, req: OverseasOrderRequest) -> dict:
        tr_id = self._TR[self._mode][req.side]
        body = {
            "CANO": kis_config.account_no,
            "ACNT_PRDT_CD": kis_config.account_product_code,
            "OVRS_EXCG_CD": req.exchange,
            "PDNO": req.symbol.upper(),
            "ORD_QTY": str(req.quantity),
            "OVRS_ORD_UNPR": f"{req.limit_price:.2f}",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": req.order_type,
        }
        return self.client.post("/uapi/overseas-stock/v1/trading/order", tr_id, body)
