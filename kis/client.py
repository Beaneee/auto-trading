"""KIS REST API 클라이언트 (저수준 HTTP 래퍼)"""
import requests
from typing import Any
from config.settings import kis_config
from kis.auth import KISAuth


class KISClient:
    def __init__(self):
        self.auth = KISAuth()
        self.session = requests.Session()

    def _headers(self, tr_id: str, extra: dict | None = None) -> dict:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.auth.get_token()}",
            "appkey": kis_config.app_key,
            "appsecret": kis_config.app_secret,
            "tr_id": tr_id,
        }
        if extra:
            headers.update(extra)
        return headers

    def get(self, path: str, tr_id: str, params: dict) -> dict[str, Any]:
        url = f"{kis_config.base_url}{path}"
        resp = self.session.get(url, headers=self._headers(tr_id), params=params)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, tr_id: str, body: dict) -> dict[str, Any]:
        url = f"{kis_config.base_url}{path}"
        resp = self.session.post(url, headers=self._headers(tr_id), json=body)
        resp.raise_for_status()
        return resp.json()
