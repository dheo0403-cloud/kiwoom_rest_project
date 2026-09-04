import streamlit as st
import pymysql
import pandas as pd
from datetime import datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go
import os
import time
import requests
from dotenv import load_dotenv

# 한국 표준시(KST) 강제 적용
os.environ["TZ"] = "Asia/Seoul"
if hasattr(time, "tzset"):
    time.tzset()

current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, '.env')
load_dotenv(env_path, override=False)

# 페이지 설정
st.set_page_config(page_title="Kiwoom Quant Automation Dashboard", layout="wide", page_icon="📈")

# CSS 스타일
st.markdown("""
<style>
    [data-testid="stMetric"] {
        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #0f3460;
    }
    [data-testid="stMetricLabel"] > div { color: #a0aec0 !important; }
    [data-testid="stMetricValue"] > div { font-size: 1.3rem !important; color: #ffffff !important; }
    [data-testid="stMetricDelta"] > div { color: #48bb78 !important; }
    .emergency-banner {
        background-color: #ffebee;
        border-left: 6px solid #f44336;
        padding: 12px 20px;
        margin-bottom: 20px;
        border-radius: 4px;
        color: #b71c1c;
        font-weight: bold;
    }
    .notif-card {
        background: #1e1e2f;
        padding: 12px 16px;
        border-radius: 8px;
        border-left: 4px solid #3b82f6;
        margin-bottom: 10px;
        color: #f1f5f9;
    }
    .notif-trade { border-left-color: #10b981; }
    .notif-alert { border-left-color: #ef4444; }
    .notif-info { border-left-color: #3b82f6; }
</style>
""", unsafe_allow_html=True)

API_BASE_URL = os.getenv("API_SERVER_URL", "http://localhost:8000")


def get_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "azure"),
        password=os.getenv("DB_PASSWORD", ""),
        db=os.getenv("DB_NAME", "kiwoom_quant_db")
    )


@st.cache_data(ttl=15)
def load_data():
    conn = get_connection()
    orders_df = pd.read_sql_query("SELECT * FROM order_history ORDER BY timestamp DESC", conn)
    balance_df = pd.read_sql_query("SELECT * FROM balance ORDER BY date ASC", conn)
    logs_df = pd.read_sql_query("SELECT * FROM logs ORDER BY timestamp DESC LIMIT 60", conn)
    conn.close()
    return orders_df, balance_df, logs_df


@st.cache_data(ttl=5)
def load_watchlist():
    conn = get_connection()
    try:
        watchlist_df = pd.read_sql_query(
            "SELECT code, COALESCE(name, code) as name, COALESCE(current_price, 0) as current_price, COALESCE(avg_volume, 0) as avg_volume, status, updated_at FROM watchlist ORDER BY current_price DESC",
            conn
        )
    except Exception:
        watchlist_df = pd.DataFrame()
    conn.close()
    return watchlist_df


@st.cache_data(ttl=10)
def load_portfolio():
    conn = get_connection()
    try:
        portfolio_df = pd.read_sql_query("SELECT * FROM portfolio", conn)
    except Exception:
        portfolio_df = pd.DataFrame()
    conn.close()
    return portfolio_df


def format_currency(val):
    """숫자를 원화 포맷으로 변환"""
    try:
        v = int(val)
        return f"{v:,}원"
    except (ValueError, TypeError):
        return str(val)


def calc_sell_pnl(orders_df):
    """매도 종목별 실현 손익 계산"""
    if orders_df.empty:
        return pd.DataFrame()

    results = []
    codes = orders_df[orders_df['side'] == 'SELL']['code'].unique()

    for code in codes:
        code_df = orders_df[orders_df['code'] == code].sort_values('timestamp')
        buys = code_df[code_df['side'] == 'BUY']
        sells = code_df[code_df['side'] == 'SELL']

        if buys.empty or sells.empty:
            continue

        avg_buy = (buys['price'] * buys['qty']).sum() / buys['qty'].sum()
        name = sells.iloc[0].get('name', code)

        for _, sell in sells.iterrows():
            pnl = (sell['price'] - avg_buy) * sell['qty']
            pnl_pct = ((sell['price'] / avg_buy) - 1) * 100 if avg_buy > 0 else 0
            results.append({
                '매도일시': sell['timestamp'],
                '종목코드': code,
                '종목명': name,
                '매수평단': int(avg_buy),
                '매도가': sell['price'],
                '수량': sell['qty'],
                '실현손익': int(pnl),
                '수익률(%)': round(pnl_pct, 2),
            })

    return pd.DataFrame(results)


# ─── 타이틀 및 상단 컨트롤 바 ─────────────────────────────────────────
st.title("📈 키움 비동기 퀀트 자동매매 관제 센터")

# ═══════════════════════════════════════════════════
# 🚨 긴급 비상 킬스위치 (Emergency Kill-Switch UI)
# ═══════════════════════════════════════════════════
st.markdown("---")
e_col1, e_col2, e_col3 = st.columns([2, 1, 1])

with e_col1:
    st.warning("⚠️ **긴급 상황 발생 시**: 아래 버튼을 클릭하면 진행 중인 모든 주문을 취소하고 보유 종목을 **즉시 시장가 전량 청산**합니다.")

with e_col2:
    if st.button("🚨 긴급 전량 청산 (Kill-Switch)", type="primary", use_container_width=True):
        try:
            res = requests.post(f"{API_BASE_URL}/api/bot/emergency-stop", timeout=3)
            if res.status_code == 200:
                st.error("🚨 [비상 킬스위치 발동 완료] 모든 보유 종목의 시장가 매도 주문이 접수되었습니다!")
                st.cache_data.clear()
            else:
                st.error(f"❌ 킬스위치 호출 실패: {res.text}")
        except Exception as e:
            st.error(f"❌ API 서버 연결 실패: {e}")

with e_col3:
    if st.button("🔄 봇 상태 동기화", use_container_width=True):
        try:
            requests.post(f"{API_BASE_URL}/api/bot/control", json={"action": "REFRESH"}, timeout=2)
            st.success("✅ 계좌 및 감시 유니버스가 동기화되었습니다.")
            st.cache_data.clear()
        except Exception:
            st.cache_data.clear()

st.markdown("---")

try:
    orders, balance, logs = load_data()
    watchlist = load_watchlist()
    portfolio_data = load_portfolio()

    # ═══════════════════════════════════════════════════
    # 1. 핵심 요약 지표 (KPI Metrics)
    # ═══════════════════════════════════════════════════
    col1, col2, col3, col4, col5 = st.columns(5)
    if not balance.empty:
        latest = balance.iloc[-1]
        col1.metric("총 평가자산", format_currency(latest['total_asset']))
        col2.metric("예수금 (현금)", format_currency(latest['deposit']))
        col3.metric("누적 수익금", format_currency(latest['profit_loss']), delta=f"{latest['yield']}%")
    else:
        col1.metric("총 평가자산", "0원")
        col2.metric("예수금 (현금)", "0원")
        col3.metric("누적 수익금", "0원")

    today_orders = orders[orders['timestamp'].astype(str).str.startswith(datetime.now().strftime('%Y-%m-%d'))] if not orders.empty else pd.DataFrame()
    today_buy = len(today_orders[today_orders['side'] == 'BUY']) if not today_orders.empty else 0
    today_sell = len(today_orders[today_orders['side'] == 'SELL']) if not today_orders.empty else 0
    col4.metric("당일 매매 체결", f"매수 {today_buy} / 매도 {today_sell}")

    watching_count = len(watchlist) if not watchlist.empty else 0
    col5.metric("실시간 감시 종목", f"{watching_count}개 종목")

    # ═══════════════════════════════════════════════════
    # 2. 탭 구성 (알림 센터, 감시종목, 보유종목, 매매내역, 손익, 자산, 파라미터)
    # ═══════════════════════════════════════════════════
    tab0, tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "🔔 알림 센터 (Feed)", "📋 감시 종목", "📦 보유 종목", "💰 매매 내역",
        "📉 매도 손익", "📊 자산 추이", "🔧 시스템 로그", "⚙️ 퀀트 파라미터"
    ])

    # ── [Tab 0] 알림 센터 (Notification Center) ──
    with tab0:
        st.subheader("🔔 실시간 시스템 알림 센터 (Notification Center)")
        st.caption("카카오톡 알림 발송 내역 및 실시간 체결/손절/서킷브레이커 이벤트 피드입니다.")

        # 카카오톡 연동 상태 배너
        kakao_key = os.getenv("KAKAO_REST_API_KEY", "")
        kakao_token = os.getenv("KAKAO_ACCESS_TOKEN", "")
        if kakao_key and kakao_token:
            st.success("📱 **카카오톡 '나에게 보내기' 연동 활성화됨** (실시간 체결 시 카카오톡으로 푸시 발송)")
        else:
            st.info("💡 **알림 안내**: `.env`에 `KAKAO_REST_API_KEY` 및 `KAKAO_ACCESS_TOKEN`을 설정하면 카카오톡 푸시를 수신할 수 있습니다. (현재 대시보드 피드로 수신 중)")

        if not logs.empty:
            # 주요 체결 및 알림 로그 필터링
            notif_logs = logs[logs['level'].isin(['TRADE', 'CRITICAL', 'WARNING', 'SYSTEM', 'MANUAL_ORDER'])].copy()
            if not notif_logs.empty:
                for _, row in notif_logs.head(20).iterrows():
                    lvl = row['level']
                    ts = row['timestamp']
                    msg = row['message']
                    card_class = "notif-trade" if lvl == "TRADE" else ("notif-alert" if lvl in ["CRITICAL", "WARNING"] else "notif-info")
                    icon = "🔥 [체결]" if lvl == "TRADE" else ("🚨 [경보]" if lvl in ["CRITICAL", "WARNING"] else "ℹ️ [시스템]")

                    st.markdown(f"""
                    <div class="notif-card {card_class}">
                        <strong>{icon} [{ts}]</strong> <code>{lvl}</code><br>
                        {msg}
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.info("새로운 알림이 없습니다.")
        else:
            st.info("알림 내역이 없습니다.")

    # ── [Tab 1] 감시 종목 ──
    with tab1:
        st.subheader("📋 실시간 감시 종목 현황")
        if not watchlist.empty:
            display_df = watchlist.copy()
            display_df.columns = ['종목코드', '종목명', '현재가', '거래량', '상태', '최종갱신']
            display_df['현재가'] = display_df['현재가'].apply(lambda x: f"{int(x):,}원" if int(x) > 0 else "조회 중...")
            display_df['거래량'] = display_df['거래량'].apply(lambda x: f"{int(x):,}" if int(x) > 0 else "-")
            display_df['상태'] = display_df['상태'].apply(lambda x: "🟢 감시중" if x == 'WATCHING' else "🔴 " + str(x))
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            st.caption(f"총 {len(watchlist)}개 종목 실시간 인메모리 링버퍼 감시 중")
        else:
            st.info("현재 감시 중인 종목이 없습니다.")

    # ── [Tab 2] 보유 종목 ──
    with tab2:
        st.subheader("📦 현재 보유 포지션 현황")
        if not portfolio_data.empty:
            holdings = []
            for _, row in portfolio_data.iterrows():
                code = row['code']
                name = row['name']
                net_qty = row['qty']
                avg_price = row['buy_price']
                cur_price = row.get('current_price', avg_price)

                eval_amt = cur_price * net_qty if cur_price > 0 else avg_price * net_qty
                pnl = (cur_price - avg_price) * net_qty if cur_price > 0 else 0
                pnl_pct = round(((cur_price / avg_price) - 1) * 100, 2) if cur_price > 0 and avg_price > 0 else 0
                holdings.append({
                    '종목코드': code, '종목명': name, '보유수량': int(net_qty),
                    '평균단가': avg_price, '현재가': cur_price if cur_price > 0 else '-',
                    '평가금액': eval_amt, '평가손익': pnl, '수익률(%)': pnl_pct
                })

            if holdings:
                h_df = pd.DataFrame(holdings)
                total_eval = h_df['평가금액'].sum()
                total_pnl = h_df['평가손익'].sum()
                hc1, hc2, hc3 = st.columns(3)
                hc1.metric("보유 종목 수", f"{len(h_df)}개")
                hc2.metric("총 평가금액", format_currency(total_eval))
                pnl_delta = f"{'+' if total_pnl >= 0 else ''}{total_pnl:,}원"
                hc3.metric("총 평가손익", format_currency(abs(total_pnl)), delta=pnl_delta)

                fmt_df = h_df.copy()
                fmt_df['평균단가'] = fmt_df['평균단가'].apply(lambda x: f"{x:,}원")
                fmt_df['현재가'] = fmt_df['현재가'].apply(lambda x: f"{x:,}원" if isinstance(x, (int, float)) and x > 0 else "미확인")
                fmt_df['평가금액'] = fmt_df['평가금액'].apply(lambda x: f"{x:,}원")
                fmt_df['평가손익'] = h_df['평가손익'].apply(lambda x: f"{'+'if x>=0 else ''}{x:,}원")
                fmt_df['수익률(%)'] = h_df['수익률(%)'].apply(lambda x: f"{'+'if x>=0 else ''}{x:.2f}%")
                st.dataframe(fmt_df, use_container_width=True, hide_index=True)
            else:
                st.info("현재 보유 중인 포지션이 없습니다.")
        else:
            st.info("현재 보유 중인 종목이 없습니다 (증권사 계좌 기준).")

    # ── [Tab 3] 매매 내역 ──
    with tab3:
        st.subheader("💰 전체 매매 체결 내역")
        if not orders.empty:
            disp = orders.copy()
            if 'price' in disp.columns:
                disp['금액'] = disp.apply(lambda r: f"{int(r['price']) * int(r['qty']):,}원", axis=1)
                disp['price'] = disp['price'].apply(lambda x: f"{int(x):,}원")
            if 'side' in disp.columns:
                disp['side'] = disp['side'].apply(lambda x: "🔴 매수" if x == "BUY" else "🔵 매도")

            rename_map = {'timestamp': '일시', 'code': '종목코드', 'name': '종목명', 'side': '구분', 'qty': '수량', 'price': '단가'}
            disp = disp.rename(columns={k: v for k, v in rename_map.items() if k in disp.columns})
            st.dataframe(disp, use_container_width=True, hide_index=True)
        else:
            st.info("아직 매매 내역이 없습니다.")

    # ── [Tab 4] 매도 손익 ──
    with tab4:
        st.subheader("📉 매도 실현 손익 분석")
        if not orders.empty:
            pnl_df = calc_sell_pnl(orders)
            if not pnl_df.empty:
                total_realized = pnl_df['실현손익'].sum()
                win_cnt = len(pnl_df[pnl_df['실현손익'] > 0])
                lose_cnt = len(pnl_df[pnl_df['실현손익'] < 0])
                win_rate = round(win_cnt / len(pnl_df) * 100, 1) if len(pnl_df) > 0 else 0

                sc1, sc2, sc3, sc4 = st.columns(4)
                sc1.metric("총 실현손익", format_currency(abs(total_realized)),
                           delta=f"{'+'if total_realized>=0 else ''}{total_realized:,}원")
                sc2.metric("매도 건수", f"{len(pnl_df)}건")
                sc3.metric("승률", f"{win_rate}%", delta=f"이익 {win_cnt} / 손실 {lose_cnt}")
                sc4.metric("평균 수익률", f"{pnl_df['수익률(%)'].mean():.2f}%")

                fmt_pnl = pnl_df.copy()
                fmt_pnl['매수평단'] = fmt_pnl['매수평단'].apply(lambda x: f"{x:,}원")
                fmt_pnl['매도가'] = fmt_pnl['매도가'].apply(lambda x: f"{int(x):,}원")
                fmt_pnl['실현손익'] = pnl_df['실현손익'].apply(lambda x: f"{'+'if x>=0 else ''}{x:,}원")
                fmt_pnl['수익률(%)'] = pnl_df['수익률(%)'].apply(lambda x: f"{'+'if x>=0 else ''}{x:.2f}%")
                st.dataframe(fmt_pnl, use_container_width=True, hide_index=True)
            else:
                st.info("아직 매도 실현 기록이 없습니다.")
        else:
            st.info("매매 기록이 없습니다.")

    # ── [Tab 5] 자산 추이 ──
    with tab5:
        st.subheader("📊 일별 자산 추이 (Equity Curve)")
        if not balance.empty:
            fig = px.line(balance, x='date', y='total_asset', title="Total Asset Trend", markers=True)
            fig.update_layout(template="plotly_dark", height=400)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("자산 기록이 아직 없습니다.")

    # ── [Tab 6] 시스템 로그 ──
    with tab6:
        st.subheader("🔧 시스템 실시간 로그")
        if not logs.empty:
            for _, row in logs.iterrows():
                level = row['level']
                icon = "🔴" if level == "ERROR" else "🟢" if level == "TRADE" else "⚠️" if level == "WARNING" else "⚪"
                st.markdown(f"{icon} **[{row['timestamp']}]** `{level}` {row['message']}")
        else:
            st.info("로그가 없습니다.")

    # ── [Tab 7] 퀀트 파라미터 무중단 튜닝 ──
    with tab7:
        st.subheader("⚙️ 런타임 퀀트 파라미터 동적 조정")
        st.caption("데몬 프로세스를 재시작하지 않고도 전략 계수를 실시간 튜닝할 수 있습니다.")

        param_c1, param_c2 = st.columns(2)
        with param_c1:
            k_val = st.slider("ATR 변동성 돌파 계수 (k)", min_value=0.3, max_value=0.8, value=0.5, step=0.05)
        with param_c2:
            kelly_val = st.slider("프랙셔널 켈리 자산 배분 비중 (Kelly Fraction)", min_value=0.1, max_value=0.5, value=0.4, step=0.05)

        if st.button("🚀 파라미터 실시간 적용", type="primary"):
            try:
                res = requests.post(f"{API_BASE_URL}/api/bot/params?k_breakout={k_val}&kelly_fraction={kelly_val}", timeout=3)
                if res.status_code == 200:
                    st.success(f"✅ 파라미터 적용 완료: k={k_val}, Kelly={kelly_val}")
                else:
                    st.error(f"❌ 파라미터 적용 실패: {res.text}")
            except Exception as e:
                st.error(f"❌ API 서버 연결 실패: {e}")

except Exception as e:
    st.error(f"데이터를 불러오는 중 오류가 발생했습니다: {e}")

# 사이드바
st.sidebar.info("15초마다 자동 캐시 갱신됩니다.\n수동 새로고침: 아래 버튼 클릭")
if st.sidebar.button("🔄 데이터 강제 새로고침"):
    st.cache_data.clear()
    st.rerun()
