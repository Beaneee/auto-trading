"""주문 실행 (매수/매도/취소)"""
from dataclasses import dataclass
from enum import StrEnum
from kis.client import KISClient
from config.settings import kis_config


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "01"   # 시장가
    LIMIT = "00"    # 지정가


@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    quantity: int
    price: int = 0              # 시장가 주문 시 0
    order_type: OrderType = OrderType.MARKET


class OrderExecutor:
    # 실전/모의 TR_ID 매핑
    _TR = {
        "real": {"BUY": "TTTC0802U", "SELL": "TTTC0801U"},
        "sim":  {"BUY": "VTTC0802U", "SELL": "VTTC0801U"},
    }

    def __init__(self, client: KISClient):
        self.client = client
        self._mode = "real" if kis_config.is_real else "sim"

    def send(self, req: OrderRequest) -> dict:
        tr_id = self._TR[self._mode][req.side]
        body = {
            "CANO": kis_config.account_no,
            "ACNT_PRDT_CD": kis_config.account_product_code,
            "PDNO": req.symbol,
            "ORD_DVSN": req.order_type,
            "ORD_QTY": str(req.quantity),
            "ORD_UNPR": str(req.price),
        }
        return self.client.post("/uapi/domestic-stock/v1/trading/order-cash", tr_id, body)
