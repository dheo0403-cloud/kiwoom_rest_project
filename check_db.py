import pymysql
import pandas as pd

try:
    conn = pymysql.connect(host='localhost', user='azure', password='1Rhcemdtla!3%7', db='kiwoom_quant_db')
    
    print('--- PORTFOLIO ---')
    try:
        portfolio_df = pd.read_sql_query('SELECT * FROM portfolio', conn)
        print(portfolio_df)
    except Exception as e:
        print(f'Error reading portfolio: {e}')
        
    print('--- ORDER HISTORY ---')
    try:
        orders_df = pd.read_sql_query('SELECT * FROM order_history', conn)
        print(orders_df)
    except Exception as e:
        print(f'Error reading order_history: {e}')

    conn.close()
except Exception as e:
    print(f'Connection error: {e}')
