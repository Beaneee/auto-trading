"""시세 조회 API"""
from kis.client import KISClient


class MarketAPI:
    def __init__(self, client: KISClient):
        self.client = client

    def get_price(self, symbol: str) -> dict:
        """주식 현재가 조회"""
        return self.client.get(
            path="/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )

    def get_ohlcv(self, symbol: str, period: str = "D") -> dict:
        """주식 일/주/월 OHLCV 조회 (period: D/W/M)"""
        return self.client.get(
            path="/uapi/domestic-stock/v1/quotations/inquire-daily-price",
            tr_id="FHKST01010400",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_PERIOD_DIV_CODE": period,
                "FID_ORG_ADJ_PRC": "0",
            },
        )
