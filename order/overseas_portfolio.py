"""Overseas portfolio inquiry helpers."""
from __future__ import annotations

from config.settings import kis_config
from kis.client import KISClient
from order.portfolio import Holding, PortfolioSnapshot


class OverseasPortfolio:
    def __init__(self, client: KISClient, exchange: str = "NASD", currency: str = "USD"):
        self.client = client
        self.exchange = exchange
        self.currency = currency

    def get_balance(self) -> dict:
        return self.client.get(
            path="/uapi/overseas-stock/v1/trading/inquire-balance",
            tr_id="TTTS3012R" if kis_config.is_real else "VTTS3012R",
            params={
                "CANO": kis_config.account_no,
                "ACNT_PRDT_CD": kis_config.account_product_code,
                "OVRS_EXCG_CD": self.exchange,
                "TR_CRCY_CD": self.currency,
                "CTX_AREA_FK200": "",
                "CTX_AREA_NK200": "",
            },
        )

    def get_snapshot(self) -> PortfolioSnapshot:
        data = self.get_balance()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"Overseas balance inquiry failed: {data}")

        holdings: dict[str, Holding] = {}
        for row in data.get("output1") or []:
            symbol = str(row.get("ovrs_pdno") or row.get("pdno") or "").strip().upper()
            quantity = _to_int(row.get("ovrs_cblc_qty") or row.get("hldg_qty"))
            if not symbol or quantity <= 0:
                continue

            holdings[symbol] = Holding(
                symbol=symbol,
                name=str(row.get("ovrs_item_name") or row.get("prdt_name") or symbol).strip(),
                quantity=quantity,
                average_price=_to_float(row.get("pchs_avg_pric") or row.get("frcr_pchs_amt1")),
                current_price=_to_float(row.get("now_pric2") or row.get("ovrs_now_pric1") or row.get("prpr")),
            )

        summary = data.get("output2") or {}
        if isinstance(summary, list):
            summary = summary[0] if summary else {}

        cash = _to_int(
            summary.get("frcr_dncl_amt_2")
            or summary.get("frcr_drwg_psbl_amt_1")
            or summary.get("ord_psbl_cash")
        )
        total_value = _to_int(
            summary.get("tot_evlu_pfls_amt")
            or summary.get("ovrs_tot_pfls")
            or summary.get("tot_asst_amt")
        )
        if total_value <= 0:
            total_value = cash + int(sum(holding.market_value for holding in holdings.values()))

        return PortfolioSnapshot(cash=cash, total_value=total_value, holdings=holdings)


def _to_int(value) -> int:
    if value in (None, ""):
        return 0
    return int(float(str(value).replace(",", "")))


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    return float(str(value).replace(",", ""))
