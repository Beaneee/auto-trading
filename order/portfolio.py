"""잔고 및 포지션 조회"""
from kis.client import KISClient
from config.settings import kis_config


class Portfolio:
    def __init__(self, client: KISClient):
        self.client = client

    def get_balance(self) -> dict:
        """주식 잔고 조회"""
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
