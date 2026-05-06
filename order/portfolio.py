"""Portfolio and cash balance inquiry helpers."""
from dataclasses import dataclass

from config.settings import kis_config
from kis.client import KISClient


@dataclass(frozen=True)
class Holding:
    symbol: str
    name: str
    quantity: int
    average_price: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def profit_rate(self) -> float:
        if self.average_price <= 0:
            return 0.0
        return (self.current_price - self.average_price) / self.average_price * 100


@dataclass(frozen=True)
class PortfolioSnapshot:
    cash: int
    total_value: int
    holdings: dict[str, Holding]

    @property
    def occupied_slots(self) -> int:
        return len([holding for holding in self.holdings.values() if holding.quantity > 0])


class Portfolio:
    def __init__(self, client: KISClient):
        self.client = client

    def get_balance(self) -> dict:
        """Return raw KIS domestic stock balance response."""
        return self.client.get(
            path="/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id="TTTC8434R" if kis_config.is_real else "VTTC8434R",
            params={
                "CANO": kis_config.account_no,
                "ACNT_PRDT_CD": kis_config.account_product_code,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )

    def get_snapshot(self) -> PortfolioSnapshot:
        data = self.get_balance()
        if data.get("rt_cd") != "0":
            raise RuntimeError(f"Balance inquiry failed: {data}")

        summary = (data.get("output2") or [{}])[0]
        cash = _to_int(summary.get("dnca_tot_amt"))
        total_value = _to_int(summary.get("tot_evlu_amt")) or cash

        holdings: dict[str, Holding] = {}
        for row in data.get("output1") or []:
            symbol = str(row.get("pdno") or "").strip()
            quantity = _to_int(row.get("hldg_qty"))
            if not symbol or quantity <= 0:
                continue

            holdings[symbol] = Holding(
                symbol=symbol,
                name=str(row.get("prdt_name") or symbol).strip(),
                quantity=quantity,
                average_price=_to_float(row.get("pchs_avg_pric")),
                current_price=_to_float(row.get("prpr")),
            )

        return PortfolioSnapshot(cash=cash, total_value=total_value, holdings=holdings)


def _to_int(value) -> int:
    if value in (None, ""):
        return 0
    return int(float(str(value).replace(",", "")))


def _to_float(value) -> float:
    if value in (None, ""):
        return 0.0
    return float(str(value).replace(",", ""))
