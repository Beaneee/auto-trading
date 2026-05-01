from pydantic_settings import BaseSettings
from pydantic import Field


class KISConfig(BaseSettings):
    app_key: str = Field(..., env="KIS_APP_KEY")
    app_secret: str = Field(..., env="KIS_APP_SECRET")
    account_no: str = Field(..., env="KIS_ACCOUNT_NO")      # ex) "50123456"
    account_product_code: str = Field("01", env="KIS_ACCOUNT_PRODUCT_CODE")
    is_real: bool = Field(False, env="KIS_IS_REAL")         # False = 모의투자

    @property
    def base_url(self) -> str:
        return "https://openapi.koreainvestment.com:9443" if self.is_real else "https://openapivts.koreainvestment.com:29443"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


class AppConfig(BaseSettings):
    log_level: str = Field("INFO", env="LOG_LEVEL")
    log_dir: str = Field("logs", env="LOG_DIR")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


kis_config = KISConfig()
app_config = AppConfig()
