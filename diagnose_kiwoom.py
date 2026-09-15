"""
키움증권 REST API 실계좌 잔고 & 예수금 TR 종합 진단 도구 (diagnose_kiwoom.py)
- 실행 방법: python diagnose_kiwoom.py
- 역할: kt00001, kt00004, kt00005, kt00018 등 4대 계좌 TR을 직접 호출하여 실제 응답 필드와 종목 리스트를 정밀 분석
"""
import os
import sys
import json
import asyncio
import aiohttp
from dotenv import load_dotenv

load_dotenv(override=True)

APP_KEY = os.getenv("KIWOOM_APP_KEY")
APP_SECRET = os.getenv("KIWOOM_APP_SECRET")
ACCOUNT = os.getenv("KIWOOM_ACCOUNT_NO", os.getenv("KIWOOM_REAL_ACCOUNT", "6624384110"))
PASSWORD = os.getenv("KIWOOM_ACCOUNT_PASSWORD", os.getenv("KIWOOM_REAL_PASSWORD", "0000"))
IS_REAL = os.getenv("IS_REAL", "true").lower() in ("true", "1", "yes")

BASE_URL = "https://openapi.kiwoom.com" if IS_REAL else "https://openapivts.kiwoom.com"


async def get_access_token(session: aiohttp.ClientSession) -> str:
    url = f"{BASE_URL}/oauth2/token"
    payload = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    headers = {"Content-Type": "application/json"}
    print(f"🔑 [OAuth2] 토큰 발급 요청 중... (URL: {url})")
    async with session.post(url, json=payload, headers=headers) as resp:
        if resp.status != 200:
            text = await resp.text()
            print(f"❌ [OAuth2 토큰 실패] Status: {resp.status}, Body: {text}")
            return ""
        data = await resp.json()
        token = data.get("access_token", "")
        print(f"✅ [OAuth2 토큰 발급 성공] Token 앞 15자리: {token[:15]}...")
        return token


async def test_tr(session: aiohttp.ClientSession, token: str, api_id: str, title: str, payload: dict):
    print("\n" + "=" * 70)
    print(f"📡 [진단] {title} ({api_id})")
    print(f"   Payload: {json.dumps(payload, ensure_ascii=False)}")
    print("=" * 70)

    url = f"{BASE_URL}/api/dostk/acnt"
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {token}",
        "api-id": api_id
    }

    try:
        async with session.post(url, json=payload, headers=headers) as resp:
            status = resp.status
            try:
                data = await resp.json()
            except Exception:
                text = await resp.text()
                data = {"raw_text": text}

            print(f"HTTP Status: {status}")
            print(f"응답 JSON 전문:\n{json.dumps(data, indent=2, ensure_ascii=False)}")

            # 주요 키 분석 요약
            if isinstance(data, dict):
                rt_cd = data.get("rt_cd") or data.get("return_code")
                msg = data.get("msg1") or data.get("return_msg") or ""
                print(f"\n📊 [필드 분석 요약] rt_cd: {rt_cd} | msg: {msg}")

                # 1) 요약 필드
                for k in ["tot_evlu_amt", "d2_deposit", "d2_auto_amt", "prvs_rcdl_excc_amt", "entr", "dnca_tot_amt", "sub_amt", "ord_psbl_cash"]:
                    if k in data:
                        print(f"   - {k}: {data[k]}")

                # 2) 리스트 필드 (보유 종목)
                for k in ["output", "output1", "output2", "list", "acnt_dtl_list", "holdings"]:
                    v = data.get(k)
                    if isinstance(v, list):
                        print(f"   - 리스트 [{k}]: {len(v)}건 발견")
                        for idx, item in enumerate(v[:5]):
                            if isinstance(item, dict):
                                code = item.get("stk_cd") or item.get("pdno") or item.get("code") or item.get("stck_shrn_iscd")
                                name = item.get("stk_nm") or item.get("name") or item.get("hts_kor_isnm")
                                qty = item.get("hldg_qty") or item.get("qty") or item.get("ccls_qty_sum")
                                price = item.get("prpr") or item.get("current_price") or item.get("cur_prc")
                                pchs = item.get("pchs_avg_pric") or item.get("buy_price")
                                print(f"     [{idx+1}] 코드: {code} | 종목명: {name} | 수량: {qty} | 현재가: {price} | 매입가: {pchs}")

    except Exception as e:
        print(f"❌ [요청 예외] {e}")


async def main():
    print("=" * 70)
    print("🚀 [키움증권 REST API 실계좌 4대 TR 종합 진단 도구]")
    print(f"• 환경: {'REAL (실전투자)' if IS_REAL else 'MOCK (모의투자)'}")
    print(f"• 계좌번호: {ACCOUNT}")
    print("=" * 70)

    clean_acc = str(ACCOUNT).replace("-", "").strip()

    async with aiohttp.ClientSession() as session:
        token = await get_access_token(session)
        if not token:
            print("❌ 토큰 발급에 실패하여 진단을 중단합니다.")
            return

        # 1. kt00001 (예수금상세현황) - qry_tp 3(D+2), 2, 1
        await test_tr(session, token, "kt00001", "예수금상세현황 (qry_tp=3: D+2추정)", {
            "dmst_stex_tp": "KRX", "accNo": clean_acc, "accPwd": PASSWORD, "qry_tp": "3"
        })
        await test_tr(session, token, "kt00001", "예수금상세현황 (qry_tp=1: 단순예수금)", {
            "dmst_stex_tp": "KRX", "accNo": clean_acc, "accPwd": PASSWORD, "qry_tp": "1"
        })

        # 2. kt00005 (체결잔고요청 - 순수 페이로드)
        await test_tr(session, token, "kt00005", "체결잔고조회 (순수 페이로드)", {
            "dmst_stex_tp": "KRX", "accNo": clean_acc, "accPwd": PASSWORD
        })

        # 3. kt00018 (계좌평가잔고개별합산)
        await test_tr(session, token, "kt00018", "계좌평가잔고개별합산 (qry_tp=0)", {
            "dmst_stex_tp": "KRX", "accNo": clean_acc, "accPwd": PASSWORD, "qry_tp": "0"
        })

        # 4. kt00004 (계좌평가잔고내역)
        await test_tr(session, token, "kt00004", "계좌평가잔고내역 (qry_tp=1)", {
            "dmst_stex_tp": "KRX", "accNo": clean_acc, "accPwd": PASSWORD, "qry_tp": "1"
        })
        await test_tr(session, token, "kt00004", "계좌평가잔고내역 (qry_tp=0)", {
            "dmst_stex_tp": "KRX", "accNo": clean_acc, "accPwd": PASSWORD, "qry_tp": "0"
        })

    print("\n" + "=" * 70)
    print("🎉 [진단 완료] 모든 TR 호출 및 결과 분석이 완료되었습니다.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
