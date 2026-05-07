"""KIS REST API client."""
import os
import time
from typing import Any

import requests

from config.settings import kis_config
from kis.auth import KISAuth


_MIN_INTERVAL = float(os.getenv("KIS_MIN_INTERVAL", "2.0"))
_TIMEOUT = 10
_MAX_RETRIES = 3


class KISClient:
    def __init__(self):
        self.auth = KISAuth()
        self.session = requests.Session()
        self._last_call: float = 0.0

    @property
    def min_interval(self) -> float:
        return _MIN_INTERVAL

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
            "custtype": "P",
        }
        if extra:
            headers.update(extra)
        return headers

    def _raise_for_status(self, resp) -> None:
        if not resp.ok:
            raise Exception(f"HTTP {resp.status_code}: {resp.text}")

    def get(self, path: str, tr_id: str, params: dict) -> dict[str, Any]:
        url = f"{kis_config.base_url}{path}"
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                self._throttle()
                resp = self.session.get(url, headers=self._headers(tr_id), params=params, timeout=_TIMEOUT)
                self._raise_for_status(resp)
                data = resp.json()
                if self._is_token_expired_response(data) and attempt < _MAX_RETRIES:
                    self.auth.clear_token()
                    continue
                return data
            except requests.RequestException:
                if attempt == _MAX_RETRIES:
                    raise
                time.sleep(attempt * 1.5)
        raise RuntimeError("unreachable")

    def post(self, path: str, tr_id: str, body: dict) -> dict[str, Any]:
        url = f"{kis_config.base_url}{path}"
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                self._throttle()
                resp = self.session.post(url, headers=self._headers(tr_id), json=body, timeout=_TIMEOUT)
                self._raise_for_status(resp)
                data = resp.json()
                if self._is_token_expired_response(data) and attempt < _MAX_RETRIES:
                    self.auth.clear_token()
                    continue
                return data
            except requests.RequestException:
                if attempt == _MAX_RETRIES:
                    raise
                time.sleep(attempt * 1.5)
        raise RuntimeError("unreachable")

    @staticmethod
    def _is_token_expired_response(data: dict[str, Any]) -> bool:
        message = str(data.get("msg1") or "")
        code = str(data.get("msg_cd") or "")
        return code == "EGW00123" or "token" in message.lower()
