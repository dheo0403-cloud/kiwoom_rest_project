-- 모의 TOM 전용 봇(kiwoom-bot-mock)의 DB: 주문·잔고·로그·상태만. 시세(daily_ohlcv·stock_master)는 운영 DB를 읽기만 한다.
-- 실행(승인 후 1회): mysql -h <DB_HOST> -u <DB_USER> -p < deploy/mock_db.sql
-- 운영 DB(kiwoom_quant_db)는 구조만 복사하고 데이터는 건드리지 않는다. 이미 있으면 그대로 둔다(IF NOT EXISTS).
CREATE DATABASE IF NOT EXISTS kiwoom_mock_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS kiwoom_mock_db.logs LIKE kiwoom_quant_db.logs;
CREATE TABLE IF NOT EXISTS kiwoom_mock_db.order_history LIKE kiwoom_quant_db.order_history;
CREATE TABLE IF NOT EXISTS kiwoom_mock_db.balance LIKE kiwoom_quant_db.balance;
CREATE TABLE IF NOT EXISTS kiwoom_mock_db.portfolio LIKE kiwoom_quant_db.portfolio;
CREATE TABLE IF NOT EXISTS kiwoom_mock_db.watchlist LIKE kiwoom_quant_db.watchlist;
CREATE TABLE IF NOT EXISTS kiwoom_mock_db.manual_orders LIKE kiwoom_quant_db.manual_orders;
CREATE TABLE IF NOT EXISTS kiwoom_mock_db.bot_state LIKE kiwoom_quant_db.bot_state;
CREATE TABLE IF NOT EXISTS kiwoom_mock_db.buy_blocklist LIKE kiwoom_quant_db.buy_blocklist;
CREATE TABLE IF NOT EXISTS kiwoom_mock_db.skipped_signals LIKE kiwoom_quant_db.skipped_signals;
CREATE TABLE IF NOT EXISTS kiwoom_mock_db.stock_master LIKE kiwoom_quant_db.stock_master;
