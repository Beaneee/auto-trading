"""Legacy rulebook strategy for overseas stocks."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from kis.overseas import OverseasMarketAPI, parse_overseas_price
from order.executor import OrderSide
from order.overseas_executor import OverseasOrderRequest
from order.portfolio import Holding, PortfolioSnapshot
from strategy.legacy_rulebook import _add_indicators
from strategy.legacy_rulebook import _safe_ratio


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
    volume_surge_ratio = 1.8

    def __init__(
        self,
        market: OverseasMarketAPI,
        symbols: list[tuple[str, str]],
        snapshot: PortfolioSnapshot,
        market_guard_active: bool = False,
        buy_slippage_pct: float = 0.7,
        sell_slippage_pct: float = 0.7,
        max_slots: int = 3,
        total_budget: int | None = None,
    ):
        self.market = market
        self.symbols = [(symbol.upper(), exchange.upper()) for symbol, exchange in symbols]
        self.snapshot = snapshot
        self.market_guard_active = market_guard_active
        self.buy_slippage_pct = buy_slippage_pct
        self.sell_slippage_pct = sell_slippage_pct
        self.max_slots = max_slots
        self.total_budget = total_budget

    @property
    def slot_budget(self) -> int:
        total_value = self.total_budget if self.total_budget else self.snapshot.total_value
        return total_value // self.max_slots if self.max_slots > 0 else 0

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

        volume_ratio = _safe_ratio(curr["volume"], curr["volume_ma20"])
        body_position = _safe_ratio(curr["close"] - curr["low"], curr["high"] - curr["low"])
        prior_high = df["high"].iloc[-6:-1].max()

        if volume_ratio >= self.volume_surge_ratio:
            confirmations.append(f"volume surge {volume_ratio:.1f}x")
        if curr["close"] > curr["open"] and body_position >= 0.65:
            confirmations.append("bullish close near high")
        if curr["close"] > prior_high:
            confirmations.append("breakout above recent high")
        if curr["close"] > curr["ma5"] and curr["ma5"] >= curr["ma20"]:
            confirmations.append("price above rising short trend")
        if curr["rsi14"] > prev["rsi14"] and curr["rsi14"] >= 50:
            confirmations.append("rsi improving above 50")
        if curr["macd_hist"] > prev["macd_hist"] and curr["macd_hist"] > 0:
            confirmations.append("macd momentum expanding")

        has_volume_surge = any(reason.startswith("volume surge") for reason in confirmations)
        bullish_score = len(confirmations) - (1 if has_volume_surge else 0)
        if not has_volume_surge:
            return OverseasDecision(symbol, exchange, None, 0, 0, confirmations or [f"no volume surge {volume_ratio:.1f}x"], len(confirmations))
        if bullish_score < 2:
            return OverseasDecision(symbol, exchange, None, 0, 0, confirmations or ["volume surge without bullish confirmation"], len(confirmations))

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
        volume_fade = curr["volume"] < curr["volume_ma5"] and curr["volume"] < prev["volume"]
        momentum_fade = curr["macd_hist"] < prev["macd_hist"] or curr["rsi14"] < prev["rsi14"]
        trend_break = curr["close"] < curr["ma5"]

        if profit_rate > 0 and trend_break and (volume_fade or momentum_fade):
            reasons = [f"momentum fade exit {profit_rate:.2f}%"]
            if volume_fade:
                reasons.append("volume fading")
            if momentum_fade:
                reasons.append("momentum weakening")
            if trend_break:
                reasons.append("close below ma5")
            return OverseasDecision(symbol, exchange, OrderSide.SELL, holding.quantity, limit_price, reasons)
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
