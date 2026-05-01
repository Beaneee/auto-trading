"""전략 추상 베이스"""
from abc import ABC, abstractmethod
from order.executor import OrderRequest


class BaseStrategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate_signals(self) -> list[OrderRequest]:
        """매수/매도 신호 생성"""
        ...
