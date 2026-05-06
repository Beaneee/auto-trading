"""KIS overseas stock market-data helpers."""
from __future__ import annotations

from dataclasses import dataclass

from kis.client import KISClient


QUOTE_EXCHANGE_BY_ORDER_EXCHANGE = {
    "NASD": "NAS",
    "NYSE": "NYS",
    "AMEX": "AMS",
}


@dataclass(frozen=True)
class OverseasSymbol:
    symbol: str
    exchange: str = "NASD"

    @property
    def quote_exchange(self) -> str:
        return QUOTE_EXCHANGE_BY_ORDER_EXCHANGE.get(self.exchange, self.exchange)


class OverseasMarketAPI:
    def __init__(self, client: KISClient):
        self.client = client

    def get_price(self, symbol: str, exchange: str = "NASD") -> dict:
        item = OverseasSymbol(symbol=symbol.upper(), exchange=exchange.upper())
        return self.client.get(
            path="/uapi/overseas-price/v1/quotations/price",
            tr_id="HHDFS00000300",
            params={"AUTH": "", "EXCD": item.quote_exchange, "SYMB": item.symbol},
        )

    def get_ohlcv(self, symbol: str, exchange: str = "NASD") -> dict:
        item = OverseasSymbol(symbol=symbol.upper(), exchange=exchange.upper())
        return self.client.get(
            path="/uapi/overseas-price/v1/quotations/dailyprice",
            tr_id="HHDFS76240000",
            params={
                "AUTH": "",
                "EXCD": item.quote_exchange,
                "SYMB": item.symbol,
                "GUBN": "0",
                "BYMD": "",
                "MODP": "0",
            },
        )


def parse_overseas_price(response: dict) -> float:
    output = response.get("output") or {}
    for key in ("last", "ovrs_nmix_prpr", "stck_prpr", "base"):
        value = output.get(key)
        if value not in (None, ""):
            return float(str(value).replace(",", ""))
    raise RuntimeError(f"Could not parse overseas price response: {response}")
