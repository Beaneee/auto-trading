from pydantic_settings import BaseSettings, SettingsConfigDict


class KISConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_key: str
    app_secret: str
    account_no: str
    account_product_code: str = "01"
    is_real: bool = False

    @property
    def base_url(self) -> str:
        return "https://openapi.koreainvestment.com:9443" if self.is_real else "https://openapivts.koreainvestment.com:29443"


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = "INFO"
    log_dir: str = "logs"


kis_config = KISConfig()
app_config = AppConfig()
