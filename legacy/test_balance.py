import os
from dotenv import load_dotenv
import requests
import json
from kiwoom_rest import KiwoomRest

def main():
    load_dotenv(override=True)

    kiwoom = KiwoomRest(is_demo=True)
    token = kiwoom.get_access_token()
    print(f"Token: {token}")

    endpoints = [
        "/api/dostk/accnt/balance",
        "/api/dostk/acnt/balance",
        "/api/dostk/accnt",
        "/api/dostk/balance"
    ]

    payload = {
        "accNo": os.getenv("KIWOOM_MOCK_ACCOUNT"),
        "accPwd": "0000"
    }

    for ep in endpoints:
        url = f"{kiwoom.base_url}{ep}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "api-id": "kt00001"
        }

        print(f"Testing {url} ...")
        try:
            res = requests.post(url, headers=headers, data=json.dumps(payload))
            print(f"Status: {res.status_code}")
            print(f"Response: {res.text}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == '__main__':
    main()

