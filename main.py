"""자동매매 메인 루프"""
import time
import schedule
from kis.client import KISClient
from kis.market import MarketAPI
from order.executor import OrderExecutor
from strategy.ma_cross import MACrossStrategy
from monitor.logger import get_logger

logger = get_logger("main")


def run_strategy(strategy, executor):
    try:
        signals = strategy.generate_signals()
        for req in signals:
            result = executor.send(req)
            logger.info("주문 완료: %s %s %s주 → %s", req.symbol, req.side, req.quantity, result)
    except Exception as e:
        logger.error("전략 실행 오류: %s", e, exc_info=True)


def main():
    client = KISClient()
    market = MarketAPI(client)
    executor = OrderExecutor(client)

    strategy = MACrossStrategy(market, symbol="005930")  # 삼성전자

    schedule.every().day.at("09:05").do(run_strategy, strategy, executor)

    logger.info("자동매매 시작")
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
