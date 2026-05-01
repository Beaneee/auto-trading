"""KIS REST API 클라이언트 (저수준 HTTP 래퍼)"""
import time
import requests
from typing import Any
from config.settings import kis_config
from kis.auth import KISAuth

_MIN_INTERVAL = 1.1  # KIS API 초당 1건 제한 (모의투자 기준)


class KISClient:
    def __init__(self):
        self.auth = KISAuth()
        self.session = requests.Session()
        self._last_call: float = 0.0

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        self._last_call = time.time()

    def _headers(self, tr_id: str, extra: dict | None = None) -> dict:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.auth.get_token()}",
            "appkey": kis_config.app_key,
            "appsecret": kis_config.app_secret,
            "tr_id": tr_id,
            "custtype": "P",  # 개인: P, 법인: B
        }
        if extra:
            headers.update(extra)
        return headers

    def _raise_for_status(self, resp) -> None:
        if not resp.ok:
            raise Exception(f"HTTP {resp.status_code} — {resp.text}")

    def get(self, path: str, tr_id: str, params: dict) -> dict[str, Any]:
        self._throttle()
        url = f"{kis_config.base_url}{path}"
        resp = self.session.get(url, headers=self._headers(tr_id), params=params)
        self._raise_for_status(resp)
        return resp.json()

    def post(self, path: str, tr_id: str, body: dict) -> dict[str, Any]:
        self._throttle()
        url = f"{kis_config.base_url}{path}"
        resp = self.session.post(url, headers=self._headers(tr_id), json=body)
        self._raise_for_status(resp)
        return resp.json()
