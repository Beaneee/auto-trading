"""Legacy 1jo rulebook strategy.

The strategy converts the provided rulebook into deterministic order signals:
- portfolio value is split into three equal slots;
- one symbol can occupy at most one slot;
- entry looks for a volume expansion that is confirmed by upward price action;
- exits prioritize capital protection before profit capture.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from kis.market import MarketAPI
from order.executor import OrderRequest, OrderSide, OrderType
from order.portfolio import Holding, PortfolioSnapshot
from strategy.base import BaseStrategy


@dataclass(frozen=True)
class RulebookDecision:
    symbol: str
    side: OrderSide | None
    quantity: int
    reasons: list[str]
    score: int = 0
    replace_symbol: str | None = None

    @property
    def has_order(self) -> bool:
        return self.side is not None and self.quantity > 0


class LegacyRulebookStrategy(BaseStrategy):
    name = "legacy_rulebook"

    max_slots = 3
    take_profit_pct = 1.0
    stop_loss_pct = -2.5
    guarded_stop_loss_pct = -1.0
    volume_surge_ratio = 1.8
    replacement_min_score = 75
    replacement_gap = 20
    protected_profit_pct = 1.0

    def __init__(
        self,
        market: MarketAPI,
        symbols: list[str],
        snapshot: PortfolioSnapshot,
        market_guard_active: bool = False,
        max_slots: int = 3,
        total_budget: int | None = None,
    ):
        self.market = market
        self.symbols = symbols
        self.snapshot = snapshot
        self.market_guard_active = market_guard_active
        self.max_slots = max_slots
        self.total_budget = total_budget

    @property
    def slot_budget(self) -> int:
        if self.max_slots <= 0:
            return 0
        total_value = self.total_budget if self.total_budget else self.snapshot.total_value
        return total_value // self.max_slots

    @property
    def available_slots(self) -> int:
        return max(0, self.max_slots - self.snapshot.occupied_slots)

    def generate_signals(self) -> list[OrderRequest]:
        return [
            OrderRequest(
                symbol=decision.symbol,
                side=decision.side,
                quantity=decision.quantity,
                price=0,
                order_type=OrderType.MARKET,
            )
            for decision in self.generate_decisions()
            if decision.has_order
        ]

    def generate_decisions(self) -> list[RulebookDecision]:
        decisions: list[RulebookDecision] = []
        open_buy_slots = self.available_slots

        for symbol in self.symbols:
            try:
                df = self._load_ohlcv(symbol)
            except Exception as exc:
                decisions.append(RulebookDecision(symbol, None, 0, [f"data load failed: {exc}"]))
                continue

            if len(df) < 28:
                decisions.append(RulebookDecision(symbol, None, 0, [f"not enough candles: {len(df)}"]))
                continue

            holding = self.snapshot.holdings.get(symbol)
            if holding:
                decisions.append(self._exit_decision(symbol, holding, df))
                continue

            if open_buy_slots <= 0:
                replacement = self._replacement_decision(symbol, df)
                decisions.append(replacement if replacement else RulebookDecision(symbol, None, 0, ["no empty slot"]))
                continue

            decision = self._entry_decision(symbol, df)
            if decision.has_order:
                open_buy_slots -= 1
            decisions.append(decision)

        return decisions

    def _entry_decision(self, symbol: str, df: pd.DataFrame) -> RulebookDecision:
        if self.market_guard_active:
            return RulebookDecision(symbol, None, 0, ["market guard active"])

        prev = df.iloc[-2]
        curr = df.iloc[-1]

        confirmations: list[str] = []
        volume_ratio = _safe_ratio(curr["volume"], curr["volume_ma20"])
        body_position = _safe_ratio(curr["close"] - curr["low"], curr["high"] - curr["low"])
        prior_high = df["high"].iloc[-6:-1].max()

        candidate_score, confirmations = score_momentum_candidate(df)

        has_volume_surge = any(reason.startswith("volume surge") for reason in confirmations)
        bullish_score = len(confirmations) - (1 if has_volume_surge else 0)
        if not has_volume_surge:
            return RulebookDecision(symbol, None, 0, confirmations or [f"no volume surge {volume_ratio:.1f}x"], candidate_score)

        if bullish_score < 2:
            return RulebookDecision(symbol, None, 0, confirmations or ["volume surge without bullish confirmation"], candidate_score)

        quantity = self._buy_quantity(curr["close"])
        if quantity <= 0:
            return RulebookDecision(symbol, None, 0, ["slot budget or cash is too small"], candidate_score)

        return RulebookDecision(symbol, OrderSide.BUY, quantity, confirmations, candidate_score)

    def _replacement_decision(self, symbol: str, df: pd.DataFrame) -> RulebookDecision | None:
        candidate_score, reasons = score_momentum_candidate(df)
        has_volume_surge = any(reason.startswith("volume surge") for reason in reasons)
        bullish_score = len(reasons) - (1 if has_volume_surge else 0)
        if candidate_score < self.replacement_min_score or not has_volume_surge or bullish_score < 2:
            return None

        weakest_symbol, weakest_holding, weakest_score, weakest_reasons = self._weakest_holding()
        if weakest_holding is None:
            return None
        if weakest_holding.profit_rate >= self.protected_profit_pct and weakest_score >= 60:
            return None
        gap = candidate_score - weakest_score
        if gap < self.replacement_gap:
            return None

        quantity = self._buy_quantity(df.iloc[-1]["close"])
        if quantity <= 0:
            return RulebookDecision(symbol, None, 0, [f"replacement candidate score {candidate_score}, but no buy quantity"], candidate_score)
        return RulebookDecision(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=quantity,
            reasons=[
                f"replacement candidate score {candidate_score}",
                f"replace {weakest_symbol} score {weakest_score}, gap {gap}",
                *reasons,
            ],
            score=candidate_score,
            replace_symbol=weakest_symbol,
        )

    def _weakest_holding(self) -> tuple[str | None, Holding | None, int, list[str]]:
        weakest_symbol = None
        weakest_holding = None
        weakest_score = 101
        weakest_reasons: list[str] = []
        for symbol, holding in self.snapshot.holdings.items():
            try:
                df = self._load_ohlcv(symbol)
                score, reasons = score_holding_strength(df, holding)
            except Exception:
                score, reasons = 0, ["holding score unavailable"]
            if score < weakest_score:
                weakest_symbol = symbol
                weakest_holding = holding
                weakest_score = score
                weakest_reasons = reasons
        return weakest_symbol, weakest_holding, weakest_score, weakest_reasons

    def _exit_decision(self, symbol: str, holding: Holding, df: pd.DataFrame) -> RulebookDecision:
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        profit_rate = holding.profit_rate
        stop_loss = self.guarded_stop_loss_pct if self.market_guard_active else self.stop_loss_pct

        if profit_rate <= stop_loss:
            return RulebookDecision(symbol, OrderSide.SELL, holding.quantity, [f"stop loss {profit_rate:.2f}% <= {stop_loss:.2f}%"])

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
            return RulebookDecision(symbol, OrderSide.SELL, holding.quantity, reasons)

        if profit_rate >= self.take_profit_pct:
            if curr["close"] >= curr["ma5"]:
                return RulebookDecision(symbol, None, 0, [f"trend hold {profit_rate:.2f}%, close above ma5"])
            if prev["close"] >= prev["ma5"] and curr["close"] < curr["ma5"]:
                return RulebookDecision(symbol, OrderSide.SELL, holding.quantity, [f"take profit on ma5 break {profit_rate:.2f}%"])

        return RulebookDecision(symbol, None, 0, [f"hold {profit_rate:.2f}%"])

    def _buy_quantity(self, price: float) -> int:
        if price <= 0:
            return 0
        budget = min(self.slot_budget, self.snapshot.cash)
        return int(budget // price)

    def _load_ohlcv(self, symbol: str) -> pd.DataFrame:
        raw = self.market.get_ohlcv(symbol)
        rows = raw.get("output") or []
        df = pd.DataFrame(rows)
        if df.empty:
            return df

        column_map = {
            "stck_bsop_date": "date",
            "stck_oprc": "open",
            "stck_hgpr": "high",
            "stck_lwpr": "low",
            "stck_clpr": "close",
            "acml_vol": "volume",
        }
        df = df.rename(columns=column_map)
        df = df[["date", "open", "high", "low", "close", "volume"]].copy()
        for column in ["open", "high", "low", "close", "volume"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")
        df = df.dropna().sort_values("date").reset_index(drop=True)
        return _add_indicators(df)


def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["volume_ma5"] = df["volume"].rolling(5).mean()
    df["volume_ma20"] = df["volume"].rolling(20).mean()

    rolling_std = df["close"].rolling(20).std()
    df["bb_lower"] = df["ma20"] - (rolling_std * 2)

    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, pd.NA)
    df["rsi14"] = 100 - (100 / (1 + rs))
    df.loc[(loss == 0) & (gain > 0), "rsi14"] = 100
    df["rsi14"] = df["rsi14"].fillna(50)

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    return df


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator in (0, None) or pd.isna(denominator):
        return 0.0
    return float(numerator) / float(denominator)


def score_momentum_candidate(df: pd.DataFrame) -> tuple[int, list[str]]:
    score, reasons, _ = score_momentum_candidate_detail(df)
    return score, reasons


def score_momentum_candidate_detail(df: pd.DataFrame) -> tuple[int, list[str], list[tuple[str, int]]]:
    prev = df.iloc[-2]
    curr = df.iloc[-1]
    score = 0
    reasons: list[str] = []
    details: list[tuple[str, int]] = []
    volume_ratio = _safe_ratio(curr["volume"], curr["volume_ma20"])
    body_position = _safe_ratio(curr["close"] - curr["low"], curr["high"] - curr["low"])
    prior_high = df["high"].iloc[-6:-1].max()

    if volume_ratio >= 1.8:
        score += 25
        reasons.append(f"volume surge {volume_ratio:.1f}x")
        details.append((f"volume surge {volume_ratio:.1f}x", 25))
    if volume_ratio >= 3.0:
        score += 10
        reasons.append("major volume expansion")
        details.append(("major volume expansion", 10))
    if curr["close"] > curr["open"] and body_position >= 0.65:
        score += 15
        reasons.append("bullish close near high")
        details.append(("bullish close near high", 15))
    if curr["close"] > prior_high:
        score += 20
        reasons.append("breakout above recent high")
        details.append(("breakout above recent high", 20))
    if curr["close"] > curr["ma5"] and curr["ma5"] >= curr["ma20"]:
        score += 10
        reasons.append("price above rising short trend")
        details.append(("price above rising short trend", 10))
    if curr["rsi14"] > prev["rsi14"] and curr["rsi14"] >= 50:
        score += 10
        reasons.append("rsi improving above 50")
        details.append(("rsi improving above 50", 10))
    if curr["macd_hist"] > prev["macd_hist"] and curr["macd_hist"] > 0:
        score += 10
        reasons.append("macd momentum expanding")
        details.append(("macd momentum expanding", 10))
    return min(score, 100), reasons, details


def score_holding_strength(df: pd.DataFrame, holding: Holding) -> tuple[int, list[str]]:
    score, reasons, _ = score_holding_strength_detail(df, holding)
    return score, reasons


def score_holding_strength_detail(df: pd.DataFrame, holding: Holding) -> tuple[int, list[str], list[tuple[str, int]]]:
    prev = df.iloc[-2]
    curr = df.iloc[-1]
    score = 50
    reasons: list[str] = []
    details: list[tuple[str, int]] = [("base holding score", 50)]
    if holding.profit_rate > 0:
        score += 10
        reasons.append("profitable")
        details.append(("profitable", 10))
    else:
        score -= 10
        reasons.append("losing")
        details.append(("losing", -10))
    if curr["close"] >= curr["ma5"]:
        score += 15
        reasons.append("above ma5")
        details.append(("above ma5", 15))
    else:
        score -= 20
        reasons.append("below ma5")
        details.append(("below ma5", -20))
    if curr["rsi14"] >= prev["rsi14"]:
        score += 10
        reasons.append("rsi stable")
        details.append(("rsi stable", 10))
    else:
        score -= 10
        reasons.append("rsi weakening")
        details.append(("rsi weakening", -10))
    if curr["macd_hist"] >= prev["macd_hist"]:
        score += 10
        reasons.append("macd stable")
        details.append(("macd stable", 10))
    else:
        score -= 10
        reasons.append("macd weakening")
        details.append(("macd weakening", -10))
    if curr["volume"] >= curr["volume_ma5"]:
        score += 5
        reasons.append("volume supported")
        details.append(("volume supported", 5))
    return max(0, min(score, 100)), reasons, details
