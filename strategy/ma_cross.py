"""이동평균 교차 전략 (예시)"""
import pandas as pd
from kis.market import MarketAPI
from order.executor import OrderRequest, OrderSide, OrderType
from strategy.base import BaseStrategy


class MACrossStrategy(BaseStrategy):
    name = "ma_cross"

    def __init__(self, market: MarketAPI, symbol: str, short: int = 5, long: int = 20):
        self.market = market
        self.symbol = symbol
        self.short = short
        self.long = long

    def generate_signals(self) -> list[OrderRequest]:
        raw = self.market.get_ohlcv(self.symbol)
        df = pd.DataFrame(raw["output"])
        df["close"] = df["stck_clpr"].astype(float)
        df = df.sort_values("stck_bsop_date")

        df["ma_short"] = df["close"].rolling(self.short).mean()
        df["ma_long"] = df["close"].rolling(self.long).mean()

        prev, curr = df.iloc[-2], df.iloc[-1]
        signals = []

        # 골든 크로스 → 매수
        if prev["ma_short"] < prev["ma_long"] and curr["ma_short"] > curr["ma_long"]:
            signals.append(OrderRequest(self.symbol, OrderSide.BUY, quantity=1, order_type=OrderType.MARKET))
        # 데드 크로스 → 매도
        elif prev["ma_short"] > prev["ma_long"] and curr["ma_short"] < curr["ma_long"]:
            signals.append(OrderRequest(self.symbol, OrderSide.SELL, quantity=1, order_type=OrderType.MARKET))

        return signals
