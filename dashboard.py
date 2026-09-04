import streamlit as st
import pymysql
import pandas as pd
from datetime import datetime, timedelta
import plotly.express as px
import plotly.graph_objects as go
import os
import time
from dotenv import load_dotenv

# 한국 표준시(KST) 강제 적용
os.environ["TZ"] = "Asia/Seoul"
if hasattr(time, "tzset"):
    time.tzset()

current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, '.env')
load_dotenv(env_path, override=False)

# 페이지 설정
st.set_page_config(page_title="Stock Automation Dashboard", layout="wide")

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
    .buy-row { color: #ff4444; font-weight: bold; }
    .sell-row { color: #4488ff; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

def get_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "mariadb-vm-svc"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "azure"),
        password=os.getenv("DB_PASSWORD", "1Rhcemdtla!3%7"),
        db=os.getenv("DB_NAME", "kiwoom_quant_db")
    )

@st.cache_data(ttl=30)
def load_data():
    conn = get_connection()
    orders_df = pd.read_sql_query("SELECT * FROM order_history ORDER BY timestamp DESC", conn)
    balance_df = pd.read_sql_query("SELECT * FROM balance ORDER BY date ASC", conn)
    logs_df = pd.read_sql_query("SELECT * FROM logs ORDER BY timestamp DESC LIMIT 50", conn)
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

@st.cache_data(ttl=30)
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
    """매도 종목별 손익 계산 (단순 평균단가 기준)"""
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


# ─── 타이틀 ─────────────────────────────────────────
st.title("📈 자동주식매매 실시간 대시보드")

try:
    orders, balance, logs = load_data()
    watchlist = load_watchlist()
    portfolio_data = load_portfolio()

    # ═══════════════════════════════════════════════════
    # 1. 상단 요약 지표
    # ═══════════════════════════════════════════════════
    col1, col2, col3, col4, col5 = st.columns(5)
    if not balance.empty:
        latest = balance.iloc[-1]
        col1.metric("총 자산", format_currency(latest['total_asset']))
        col2.metric("예수금", format_currency(latest['deposit']))
        col3.metric("누적 수익", format_currency(latest['profit_loss']), delta=f"{latest['yield']}%")
    else:
        col1.metric("총 자산", "0원")
        col2.metric("예수금", "0원")
        col3.metric("누적 수익", "0원")

    today_orders = orders[orders['timestamp'].astype(str).str.startswith(datetime.now().strftime('%Y-%m-%d'))] if not orders.empty else pd.DataFrame()
    today_buy = len(today_orders[today_orders['side'] == 'BUY']) if not today_orders.empty else 0
    today_sell = len(today_orders[today_orders['side'] == 'SELL']) if not today_orders.empty else 0
    col4.metric("당일 매매", f"매수 {today_buy} / 매도 {today_sell}")

    watching_count = len(watchlist) if not watchlist.empty else 0
    col5.metric("감시 종목", f"{watching_count}개")

    # ═══════════════════════════════════════════════════
    # 2. 탭 구성
    # ═══════════════════════════════════════════════════
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📋 감시 종목", "📦 보유 종목", "💰 매매 내역", "📉 매도 손익", "📊 자산 추이", "🔧 시스템 로그"
    ])

    # ── 감시 종목 ──
    with tab1:
        st.subheader("실시간 감시 종목 현황")
        if not watchlist.empty:
            display_df = watchlist.copy()
            display_df.columns = ['종목코드', '종목명', '현재가', '거래량', '상태', '최종갱신']
            display_df['현재가'] = display_df['현재가'].apply(lambda x: f"{int(x):,}원" if int(x) > 0 else "조회 중...")
            display_df['거래량'] = display_df['거래량'].apply(lambda x: f"{int(x):,}" if int(x) > 0 else "-")
            
            def format_time_kst(dt):
                if pd.isna(dt): return "-"
                # 만약 DB 시간이 UTC로 들어와 있으면 9시간 더해서 KST로 표시
                if hasattr(dt, 'hour'):
                    # 현재 한국 시간과 비교하여 8~10시간 차이가 나면 +9시간 보정
                    from datetime import datetime, timedelta
                    now_kst = datetime.now()
                    if abs((now_kst.hour - dt.hour) % 24) >= 8:
                        dt = dt + timedelta(hours=9)
                    return dt.strftime('%H:%M:%S')
                return str(dt)

            display_df['최종갱신'] = display_df['최종갱신'].apply(format_time_kst)
            display_df['상태'] = display_df['상태'].apply(lambda x: "🟢 감시중" if x == 'WATCHING' else "🔴 " + str(x))
            st.dataframe(display_df, width="stretch", hide_index=True)
            priced = watchlist[watchlist['current_price'] > 0]
            st.caption(f"총 {len(watchlist)}개 감시 중 | {len(priced)}개 가격 확인됨")
        else:
            st.info("현재 감시 중인 종목이 없습니다.")

    # ── 보유 종목 (DB portfolio 기반) ──
    with tab2:
        st.subheader("📦 현재 보유 종목 현황")
        if not portfolio_data.empty:
            holdings = []
            for _, row in portfolio_data.iterrows():
                code = row['code']
                name = row['name']
                net_qty = row['qty']
                avg_price = row['buy_price']
                
                # 감시 종목에서 현재가 가져오기 (혹은 portfolio에 저장된 current_price 사용)
                cur_price = row.get('current_price', 0)
                if not watchlist.empty and cur_price == 0:
                    match = watchlist[watchlist['code'] == code]
                    if not match.empty:
                        cur_price = int(match.iloc[0]['current_price'])
                        
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
                # 요약 지표
                total_eval = h_df['평가금액'].sum()
                total_pnl = h_df['평가손익'].sum()
                hc1, hc2, hc3 = st.columns(3)
                hc1.metric("보유 종목 수", f"{len(h_df)}개")
                hc2.metric("총 평가금액", format_currency(total_eval))
                pnl_delta = f"{'+' if total_pnl >= 0 else ''}{total_pnl:,}원"
                hc3.metric("총 평가손익", format_currency(abs(total_pnl)), delta=pnl_delta)

                # 포맷팅
                fmt_df = h_df.copy()
                fmt_df['평균단가'] = fmt_df['평균단가'].apply(lambda x: f"{x:,}원")
                fmt_df['현재가'] = fmt_df['현재가'].apply(lambda x: f"{x:,}원" if isinstance(x, (int, float)) and x > 0 else "미확인")
                fmt_df['평가금액'] = fmt_df['평가금액'].apply(lambda x: f"{x:,}원")
                fmt_df['평가손익'] = h_df['평가손익'].apply(lambda x: f"{'+'if x>=0 else ''}{x:,}원")
                fmt_df['수익률(%)'] = h_df['수익률(%)'].apply(lambda x: f"{'+'if x>=0 else ''}{x:.2f}%")
                st.dataframe(fmt_df, use_container_width=True, hide_index=True)
            else:
                st.info("현재 보유 중인 종목이 없습니다.")

            # 수동 매도 기능 추가
            st.markdown("---")
            st.subheader("⚠️ 수동 전량 매도 (수동 주문)")
            st.caption("선택한 종목을 즉시 전량 매도 요청합니다. (봇이 최대 1~5초 내에 현재가로 매도 실행)")
            
            sell_col1, sell_col2 = st.columns([3, 1])
            with sell_col1:
                sell_target_name = st.selectbox("매도할 종목 선택", portfolio_data['name'].tolist(), label_visibility="collapsed")
            with sell_col2:
                if st.button("즉시 매도 접수", type="primary"):
                    target_row = portfolio_data[portfolio_data['name'] == sell_target_name].iloc[0]
                    target_code = target_row['code']
                    target_qty = int(target_row['qty'])
                    
                    from database import DatabaseManager
                    db = DatabaseManager()
                    if db.add_manual_order_sync(target_code, "SELL", target_qty):
                        st.success(f"✅ [{sell_target_name}] {target_qty}주 전량 매도 요청이 봇에 전달되었습니다!")
                        st.cache_data.clear()
                    else:
                        st.error("❌ 매도 요청 접수 실패. DB 상태를 확인해주세요.")

        else:
            st.info("현재 보유 중인 종목이 없습니다 (증권사 계좌 기준).")

    # ── 매매 내역 ──
    with tab3:
        st.subheader("💰 전체 매매 내역")
        if not orders.empty:
            # 필터
            fc1, fc2, fc3 = st.columns(3)
            with fc1:
                side_filter = st.selectbox("매매구분", ["전체", "BUY", "SELL"], key="side_f")
            with fc2:
                date_range = st.selectbox("기간", ["전체", "당일", "최근 7일", "최근 30일"], key="date_f")
            with fc3:
                code_list = ["전체"] + list(orders['code'].unique())
                code_filter = st.selectbox("종목", code_list, key="code_f")

            filtered = orders.copy()
            if side_filter != "전체":
                filtered = filtered[filtered['side'] == side_filter]
            if code_filter != "전체":
                filtered = filtered[filtered['code'] == code_filter]
            if date_range == "당일":
                today_str = datetime.now().strftime('%Y-%m-%d')
                filtered = filtered[filtered['timestamp'].astype(str).str.startswith(today_str)]
            elif date_range == "최근 7일":
                cutoff = datetime.now() - timedelta(days=7)
                filtered = filtered[pd.to_datetime(filtered['timestamp']) >= cutoff]
            elif date_range == "최근 30일":
                cutoff = datetime.now() - timedelta(days=30)
                filtered = filtered[pd.to_datetime(filtered['timestamp']) >= cutoff]

            # 표시 포맷
            disp = filtered.copy()
            if 'price' in disp.columns:
                disp['금액'] = disp.apply(lambda r: f"{int(r['price']) * int(r['qty']):,}원", axis=1)
                disp['price'] = disp['price'].apply(lambda x: f"{int(x):,}원")
            if 'side' in disp.columns:
                disp['side'] = disp['side'].apply(lambda x: "🔴 매수" if x == "BUY" else "🔵 매도")

            rename_map = {'timestamp': '일시', 'code': '종목코드', 'name': '종목명', 'side': '구분', 'qty': '수량', 'price': '단가'}
            disp = disp.rename(columns={k: v for k, v in rename_map.items() if k in disp.columns})

            st.dataframe(disp, use_container_width=True, hide_index=True)
            st.caption(f"총 {len(filtered)}건")
        else:
            st.info("아직 매매 내역이 없습니다.")

    # ── 매도 손익 ──
    with tab4:
        st.subheader("📉 매도 실현 손익")
        if not orders.empty:
            pnl_df = calc_sell_pnl(orders)
            if not pnl_df.empty:
                # 요약
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

                # 포맷 표시
                fmt_pnl = pnl_df.copy()
                fmt_pnl['매수평단'] = fmt_pnl['매수평단'].apply(lambda x: f"{x:,}원")
                fmt_pnl['매도가'] = fmt_pnl['매도가'].apply(lambda x: f"{int(x):,}원")
                fmt_pnl['실현손익'] = pnl_df['실현손익'].apply(lambda x: f"{'+'if x>=0 else ''}{x:,}원")
                fmt_pnl['수익률(%)'] = pnl_df['수익률(%)'].apply(lambda x: f"{'+'if x>=0 else ''}{x:.2f}%")
                st.dataframe(fmt_pnl, use_container_width=True, hide_index=True)

                # 손익 차트
                if len(pnl_df) >= 2:
                    chart_df = pnl_df.copy()
                    chart_df['누적손익'] = chart_df['실현손익'].cumsum()
                    fig = go.Figure()
                    colors = ['#22c55e' if v >= 0 else '#ef4444' for v in chart_df['실현손익']]
                    fig.add_trace(go.Bar(x=chart_df['종목명'], y=chart_df['실현손익'], name='건별 손익', marker_color=colors))
                    fig.add_trace(go.Scatter(x=chart_df['종목명'], y=chart_df['누적손익'], name='누적손익', mode='lines+markers', line=dict(color='#3b82f6', width=2)))
                    fig.update_layout(title="매도 건별 손익 & 누적 추이", template="plotly_dark", height=350)
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("아직 매도 기록이 없습니다.")
        else:
            st.info("매매 기록이 없습니다.")

    # ── 자산 추이 ──
    with tab5:
        st.subheader("📊 일별 자산 변동")
        if not balance.empty:
            fig = px.line(balance, x='date', y='total_asset', title="Total Asset Trend")
            fig.update_layout(template="plotly_dark")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("자산 기록이 아직 없습니다.")

    # ── 시스템 로그 ──
    with tab6:
        st.subheader("🔧 실시간 로그 (최근 50개)")
        if not logs.empty:
            for _, row in logs.iterrows():
                level = row['level']
                icon = "🔴" if level == "ERROR" else "🟢" if level == "TRADE" else "📋" if level == "REPORT" else "⚪"
                st.markdown(f"{icon} **[{row['timestamp']}]** `{level}` {row['message']}")
        else:
            st.info("로그가 없습니다.")

except Exception as e:
    st.error(f"데이터를 불러오는 중 오류가 발생했습니다: {e}")
    st.info("먼저 main_rest.py를 실행하여 데이터베이스를 생성해 주세요.")

# 사이드바
st.sidebar.info("30초마다 자동 캐시 갱신됩니다.\n수동 새로고침: 아래 버튼 클릭")
if st.sidebar.button("🔄 데이터 강제 새로고침"):
    st.cache_data.clear()
    st.rerun()
