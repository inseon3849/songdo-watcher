"""
텔레그램 알림
"""

import requests

from config import BOT_TOKEN, CHAT_ID


def send_message(message: str):

    if BOT_TOKEN == "" or CHAT_ID == "":
        print("텔레그램 설정 안됨")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message,
        },
        timeout=10,
    )

    print("상태코드:", response.status_code)
    print(response.text)