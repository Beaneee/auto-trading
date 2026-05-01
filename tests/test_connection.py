"""KIS API 연동 테스트 - 모의투자 환경에서 실행 권장"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kis.client import KISClient
from kis.market import MarketAPI
from order.portfolio import Portfolio

SYMBOL = "005930"  # 삼성전자


def test_token(client):
    print("\n[1] 토큰 발급 테스트")
    token = client.auth.get_token()
    assert token and len(token) > 10, "토큰이 비어있음"
    print(f"    OK — 토큰 앞 20자: {token[:20]}...")


def test_price(client):
    print("\n[2] 현재가 조회 테스트 (삼성전자)")
    market = MarketAPI(client)
    data = market.get_price(SYMBOL)
    output = data.get("output", {})
    price = output.get("stck_prpr")
    assert price, f"현재가 없음. 응답: {data}"
    print(f"    OK — 현재가: {int(price):,}원")


def test_ohlcv(client):
    print("\n[3] OHLCV 조회 테스트 (삼성전자 일봉)")
    market = MarketAPI(client)
    data = market.get_ohlcv(SYMBOL)
    rows = data.get("output", [])
    assert rows, f"데이터 없음. 응답: {data}"
    latest = rows[0]
    print(f"    OK — 최근 캔들: {latest.get('stck_bsop_date')} 종가 {int(latest.get('stck_clpr', 0)):,}원 ({len(rows)}개 조회)")


def test_balance(client):
    print("\n[4] 잔고 조회 테스트")
    portfolio = Portfolio(client)
    data = portfolio.get_balance()
    output2 = data.get("output2", [{}])
    cash = output2[0].get("dnca_tot_amt", "N/A") if output2 else "N/A"
    rt_cd = data.get("rt_cd")
    assert rt_cd == "0", f"잔고 조회 실패. rt_cd={rt_cd}, msg={data.get('msg1')}"
    print(f"    OK — 예수금: {int(cash):,}원" if cash != "N/A" else "    OK — 잔고 응답 수신")


def main():
    print("=" * 50)
    print("KIS API 연동 테스트")
    print("=" * 50)

    results = []
    client = KISClient()

    try:
        test_token(client)
        results.append(("토큰 발급", True))
    except Exception as e:
        results.append(("토큰 발급", False))
        print(f"    FAIL — {e}")
        print("\n토큰 발급 실패 시 이후 테스트 불가. .env 설정을 확인하세요.")
        _print_summary(results)
        return

    for name, fn in [
        ("현재가 조회", lambda: test_price(client)),
        ("OHLCV 조회",  lambda: test_ohlcv(client)),
        ("잔고 조회",   lambda: test_balance(client)),
    ]:
        try:
            fn()
            results.append((name, True))
        except Exception as e:
            results.append((name, False))
            import traceback
            print(f"    FAIL — {e}")
            traceback.print_exc()

    _print_summary(results)


def _print_summary(results):
    print("\n" + "=" * 50)
    print("결과 요약")
    print("=" * 50)
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} 통과")


if __name__ == "__main__":
    main()
