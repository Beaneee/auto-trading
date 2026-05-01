"""KIS OAuth2 토큰 발급 및 관리"""
import time
import requests
from dataclasses import dataclass, field
from config.settings import kis_config


@dataclass
class Token:
    access_token: str
    expires_at: float  # unix timestamp

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at - 60  # 만료 1분 전 갱신


class KISAuth:
    def __init__(self):
        self._token: Token | None = None

    def get_token(self) -> str:
        if self._token is None or self._token.is_expired():
            self._token = self._issue_token()
        return self._token.access_token

    def _issue_token(self) -> Token:
        url = f"{kis_config.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": kis_config.app_key,
            "appsecret": kis_config.app_secret,
        }
        resp = requests.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        expires_at = time.time() + int(data["expires_in"])
        return Token(access_token=data["access_token"], expires_at=expires_at)
