"""N3 애프터마켓 매매 판단 로직 테스트 (n3_trader.py)"""
from datetime import timedelta

from database import get_kst_now
from n3_trader import N3Trader, parse_watch_quotes, pick_n3_orders


def test_parse_watch_quotes_signed_and_suffix():
    data = {"return_code": 0, "atn_stk_infr": [
        {"stk_cd": "005930_NX", "cur_prc": "-233000", "sel_bid": "-233500", "cntr_tm": "195012"},
        {"stk_cd": "A000660_NX", "cur_prc": "+500000", "sel_bid": "", "cntr_tm": "194900"},
        {"stk_cd": "035420_NX", "cur_prc": "0", "cntr_tm": ""},
    ]}
    q = parse_watch_quotes(data)
    assert q == {"005930": {"cur": 233000, "ask": 233500, "tm": "195012"},
                 "000660": {"cur": 500000, "ask": 0, "tm": "194900"}}
    assert parse_watch_quotes(None) == {}


def _u(code, close, value, prev=None):
    return {"code": code, "name": f"N{code}", "close": close, "value": value, "prev_close": prev or close}


def test_pick_n3_orders_rules():
    universe = [_u("111110", 10000, 900), _u("222220", 10000, 800), _u("333330", 50000, 700),
                _u("444440", 10000, 600, prev=7000), _u("555550", 10000, 500), _u("666660", 10000, 400),
                _u("000880", 10000, 300), _u("777770", 10000, 200)]
    quotes = {
        "111110": {"cur": 9600, "ask": 9610, "tm": "195000"},   # −4% → 신호
        "222220": {"cur": 9800, "ask": 9810, "tm": "195000"},   # −2% → 신호 아님
        "333330": {"cur": 48000, "ask": 48050, "tm": "195000"}, # −4%지만 예산 2만 원으로 1주도 못 삼
        "444440": {"cur": 9000, "ask": 9010, "tm": "195000"},   # 당일 +42.9% 급등 종목 제외
        "555550": {"cur": 9000, "ask": 0, "tm": "153000"},      # 15:40 이후 NXT 체결 없음
        "666660": {"cur": 6000, "ask": 6010, "tm": "195000"},   # −40% → ±30% 초과 제외
        "000880": {"cur": 9000, "ask": 9010, "tm": "195000"},   # 정합성 제외 종목
        "777770": {"cur": 9700, "ask": 0, "tm": "195500"},      # −3% 경계 → 신호, 매도호가 없으면 현재가
    }
    picks = pick_n3_orders(universe, quotes, slots=5, budget=20000)
    assert [(p["code"], p["qty"], p["price"]) for p in picks] == [("111110", 2, 9610), ("777770", 2, 9700)]
    assert [p["code"] for p in pick_n3_orders(universe, quotes, slots=1, budget=20000)] == ["111110"]
    assert pick_n3_orders(universe, quotes, slots=0, budget=20000) == []


def test_exit_qty_caps_to_n3_quantity():
    """다른 보유분까지 팔지 않고, NXT 잔고가 안 보이면 N3 순매수 수량으로"""
    from n3_trader import exit_qty
    assert exit_qty(105, 5) == 5
    assert exit_qty(3, 5) == 3
    assert exit_qty(0, 4) == 4


def test_protects_until_deadline():
    class _Bot:
        db = client = None
    t = N3Trader(_Bot())
    t.exit_codes = {"111110": get_kst_now() + timedelta(minutes=1), "222220": get_kst_now() - timedelta(seconds=1)}
    assert t.protects("111110") and not t.protects("222220") and not t.protects("999990")
