"""Legacy rulebook strategy for overseas stocks."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from kis.overseas import OverseasMarketAPI, parse_overseas_price
from order.executor import OrderSide
from order.overseas_executor import OverseasOrderRequest
from order.portfolio import Holding, PortfolioSnapshot
from strategy.legacy_rulebook import _add_indicators


@dataclass(frozen=True)
class OverseasDecision:
    symbol: str
    exchange: str
    side: OrderSide | None
    quantity: int
    limit_price: float
    reasons: list[str]
    score: int = 0

    @property
    def has_order(self) -> bool:
        return self.side is not None and self.quantity > 0 and self.limit_price > 0


class OverseasRulebookStrategy:
    max_slots = 3
    take_profit_pct = 1.0
    stop_loss_pct = -2.5
    guarded_stop_loss_pct = -1.0

    def __init__(
        self,
        market: OverseasMarketAPI,
        symbols: list[tuple[str, str]],
        snapshot: PortfolioSnapshot,
        market_guard_active: bool = False,
        buy_slippage_pct: float = 0.7,
        sell_slippage_pct: float = 0.7,
    ):
        self.market = market
        self.symbols = [(symbol.upper(), exchange.upper()) for symbol, exchange in symbols]
        self.snapshot = snapshot
        self.market_guard_active = market_guard_active
        self.buy_slippage_pct = buy_slippage_pct
        self.sell_slippage_pct = sell_slippage_pct

    @property
    def slot_budget(self) -> int:
        return self.snapshot.total_value // self.max_slots if self.max_slots > 0 else 0

    @property
    def available_slots(self) -> int:
        return max(0, self.max_slots - self.snapshot.occupied_slots)

    def generate_orders(self) -> list[OverseasOrderRequest]:
        return [
            OverseasOrderRequest(
                symbol=decision.symbol,
                exchange=decision.exchange,
                side=decision.side,
                quantity=decision.quantity,
                limit_price=decision.limit_price,
            )
            for decision in self.generate_decisions()
            if decision.has_order
        ]

    def generate_decisions(self) -> list[OverseasDecision]:
        decisions: list[OverseasDecision] = []
        open_buy_slots = self.available_slots

        for symbol, exchange in self.symbols:
            try:
                df = self._load_ohlcv(symbol, exchange)
                current_price = parse_overseas_price(self.market.get_price(symbol, exchange))
            except Exception as exc:
                decisions.append(OverseasDecision(symbol, exchange, None, 0, 0, [f"data load failed: {exc}"]))
                continue

            if len(df) < 28:
                decisions.append(OverseasDecision(symbol, exchange, None, 0, 0, [f"not enough candles: {len(df)}"]))
                continue

            holding = self.snapshot.holdings.get(symbol)
            if holding:
                decisions.append(self._exit_decision(symbol, exchange, holding, df, current_price))
                continue

            if open_buy_slots <= 0:
                decisions.append(OverseasDecision(symbol, exchange, None, 0, 0, ["no empty slot"]))
                continue

            decision = self._entry_decision(symbol, exchange, df, current_price)
            if decision.has_order:
                open_buy_slots -= 1
            decisions.append(decision)

        return decisions

    def _entry_decision(self, symbol: str, exchange: str, df: pd.DataFrame, current_price: float) -> OverseasDecision:
        if self.market_guard_active:
            return OverseasDecision(symbol, exchange, None, 0, 0, ["market guard active"])

        prev = df.iloc[-2]
        curr = df.iloc[-1]
        confirmations: list[str] = []

        if prev["ma5"] <= prev["ma20"] and curr["ma5"] > curr["ma20"]:
            confirmations.append("ma5 crossed above ma20")
        if prev["low"] <= prev["bb_lower"] and curr["close"] > curr["open"]:
            confirmations.append("bollinger lower-band bounce")
        if (prev["rsi14"] <= 30 < curr["rsi14"]) or (prev["rsi14"] < 50 <= curr["rsi14"]):
            confirmations.append("rsi momentum cross")
        if prev["macd"] <= prev["macd_signal"] and curr["macd"] > curr["macd_signal"]:
            confirmations.append("macd signal cross")

        if curr["volume"] <= curr["volume_ma5"]:
            return OverseasDecision(symbol, exchange, None, 0, 0, ["volume below 5-day average"], len(confirmations))
        if len(confirmations) < 2:
            return OverseasDecision(symbol, exchange, None, 0, 0, confirmations or ["less than 2 confirmations"], len(confirmations))

        limit_price = round(current_price * (1 + self.buy_slippage_pct / 100), 2)
        quantity = self._buy_quantity(limit_price)
        if quantity <= 0:
            return OverseasDecision(symbol, exchange, None, 0, 0, ["slot budget or cash is too small"], len(confirmations))

        return OverseasDecision(symbol, exchange, OrderSide.BUY, quantity, limit_price, ["volume filter ok", *confirmations], len(confirmations))

    def _exit_decision(self, symbol: str, exchange: str, holding: Holding, df: pd.DataFrame, current_price: float) -> OverseasDecision:
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        profit_rate = holding.profit_rate
        stop_loss = self.guarded_stop_loss_pct if self.market_guard_active else self.stop_loss_pct
        limit_price = round(current_price * (1 - self.sell_slippage_pct / 100), 2)

        if profit_rate <= stop_loss:
            return OverseasDecision(symbol, exchange, OrderSide.SELL, holding.quantity, limit_price, [f"stop loss {profit_rate:.2f}% <= {stop_loss:.2f}%"])
        if profit_rate >= self.take_profit_pct:
            if curr["close"] >= curr["ma5"]:
                return OverseasDecision(symbol, exchange, None, 0, 0, [f"trend hold {profit_rate:.2f}%, close above ma5"])
            if prev["close"] >= prev["ma5"] and curr["close"] < curr["ma5"]:
                return OverseasDecision(symbol, exchange, OrderSide.SELL, holding.quantity, limit_price, [f"take profit on ma5 break {profit_rate:.2f}%"])
        return OverseasDecision(symbol, exchange, None, 0, 0, [f"hold {profit_rate:.2f}%"])

    def _buy_quantity(self, price: float) -> int:
        if price <= 0:
            return 0
        budget = min(self.slot_budget, self.snapshot.cash)
        return int(budget // price)

    def _load_ohlcv(self, symbol: str, exchange: str) -> pd.DataFrame:
        raw = self.market.get_ohlcv(symbol, exchange)
        rows = raw.get("output2") or raw.get("output") or []
        df = pd.DataFrame(rows)
        if df.empty:
            return df

        column_map = {
            "xymd": "date",
            "stck_bsop_date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "clos": "close",
            "close": "close",
            "tvol": "volume",
            "volume": "volume",
        }
        df = df.rename(columns=column_map)
        df = df[["date", "open", "high", "low", "close", "volume"]].copy()
        for column in ["open", "high", "low", "close", "volume"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")
        df = df.dropna().sort_values("date").reset_index(drop=True)
        return _add_indicators(df)
