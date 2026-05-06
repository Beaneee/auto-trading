"""KIS OAuth2 access-token management."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from config.settings import kis_config


TOKEN_DIR = Path(".cache")


@dataclass
class Token:
    access_token: str
    expires_at: float

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at - 300


class KISAuth:
    def __init__(self):
        self._token: Token | None = None
        self._token_path = TOKEN_DIR / f"kis_token_{'real' if kis_config.is_real else 'sim'}.json"

    def get_token(self) -> str:
        if self._token is None:
            self._token = self._load_token()
        if self._token is None or self._token.is_expired():
            self._token = self._issue_token()
            self._save_token(self._token)
        return self._token.access_token

    def _load_token(self) -> Token | None:
        if not self._token_path.exists():
            return None
        try:
            data = json.loads(self._token_path.read_text(encoding="utf-8"))
            token = Token(access_token=data["access_token"], expires_at=float(data["expires_at"]))
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            return None
        return None if token.is_expired() else token

    def _save_token(self, token: Token) -> None:
        TOKEN_DIR.mkdir(exist_ok=True)
        self._token_path.write_text(json.dumps(asdict(token)), encoding="utf-8")

    def _issue_token(self) -> Token:
        url = f"{kis_config.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": kis_config.app_key,
            "appsecret": kis_config.app_secret,
        }
        resp = requests.post(url, json=body, timeout=10)
        if resp.status_code == 403:
            raise RuntimeError(
                "KIS token request was rejected with 403. "
                "This often happens after issuing tokens too frequently. "
                "Wait a few minutes, then run again; cached tokens will be reused afterward."
            ) from None
        resp.raise_for_status()
        data = resp.json()
        expires_at = time.time() + int(data["expires_in"])
        return Token(access_token=data["access_token"], expires_at=expires_at)
