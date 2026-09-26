import os

# GitHub Actions에서는 Repository Secrets로 넣은 값이 환경변수로 전달됨.
# 로컬 PC에서 테스트할 땐 아래 환경변수를 직접 설정하거나,
# 두 번째 인자(기본값) 자리에 본인 값을 잠깐 넣어서 테스트해도 됨
# (단, 이 파일을 그대로 깃허브에 올릴 거면 기본값 자리에 실제 토큰을 넣지 말 것!).

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SITE_URL = "https://songdotennis.co.kr/"

CHECK_INTERVAL = 180  # 3분 (로컬 실행 시에만 참고용)
