"""
watcher_v2.py

송도테니스 8~14번 코트의 '주말(토/일) 06:00-08:00, 20:00-22:00' 빈자리를
감시해서 텔레그램으로 알려주는 스크립트.

기존 watcher.py는 건드리지 않고 완전히 새로 작성한 버전이며,
config.py와 telegram_bot.py는 기존 그대로 재사용한다.

기존 watcher.py 대비 바뀐 점:
- 조건 없는 고정 wait_for_timeout(500) 등을 최대한 없애고,
  실제로 화면이 바뀔 때까지만 기다리는 조건 기반 대기 사용
- 달력에 이미 표시되는 "N/8 예약 가능" 뱃지를 먼저 읽어서,
  뱃지가 없거나(아직 오픈 안 됨) 0/8인 날짜는 아예 들어가지 않고 건너뜀
- 코트 진입을 목록의 실제 텍스트("9번 코트")로 정확히 찾아서 클릭
  (화면 버튼 순서에 의존하지 않음)
- 로그인 불필요 (읽기 전용 확인이라 기존과 동일)

실행:
    python watcher_v2.py
"""

import os
import json
import re
import time
from datetime import date

from playwright.sync_api import sync_playwright

from config import SITE_URL
from telegram_bot import send_message


# ============================================================
# 설정
# ============================================================

TARGET_COURTS = [8, 9, 10, 11, 12, 13, 14]

# 평일(월~금)은 저녁만, 주말(토/일)은 아침+저녁 둘 다 확인
WEEKDAY_TIMES = ["20:00 - 22:00"]
WEEKEND_TIMES = ["06:00 - 08:00", "20:00 - 22:00"]

# 앞으로 몇 개월치를 확인할지 (현재 달 포함)
MONTHS_TO_CHECK = 2

# 확인 주기 (초). 로컬 PC에서 계속 돌릴 때만 사용됨.
CHECK_INTERVAL_SECONDS = 300

# 이미 알림 보낸 항목을 파일로 저장 (GitHub Actions는 매번 새로 실행되므로
# 메모리가 아니라 파일에 기록해야 다음 실행에서도 중복 알림을 막을 수 있음)
NOTIFIED_FILE = "notified.json"

# 환경변수 RUN_ONCE=1이면 한 번만 확인하고 종료 (GitHub Actions용)
RUN_ONCE = os.environ.get("RUN_ONCE") == "1"


def load_notified():
    if not os.path.exists(NOTIFIED_FILE):
        return set()
    try:
        with open(NOTIFIED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(tuple(item) for item in data)
    except:
        return set()


def save_notified(notified_set):
    with open(NOTIFIED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(notified_set)), f, ensure_ascii=False, indent=2)


already_notified = load_notified()


# ============================================================
# 팝업 처리 (뜰 때까지 기다렸다가 닫기, 여러 페이지짜리도 처리)
# ============================================================

def wait_and_close_popups(page, timeout=15000):

    try:
        page.get_by_role(
            "button", name="확인"
        ).wait_for(state="visible", timeout=timeout)
    except:
        pass  # 팝업이 없을 수도 있음

    for _ in range(5):

        closed = False

        try:
            page.get_by_role(
                "button", name="확인"
            ).click(timeout=1000)
            closed = True
        except:
            pass

        if not closed:
            try:
                page.get_by_role(
                    "button", name="Close"
                ).click(timeout=1000)
                closed = True
            except:
                pass

        if not closed:
            break


# ============================================================
# 사이트 접속
# ============================================================

def open_reservation_page(page):

    page.goto(SITE_URL, wait_until="domcontentloaded", timeout=90000)

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except:
        pass

    wait_and_close_popups(page)

    reservation_button = page.get_by_role("button", name="예약").first
    reservation_button.wait_for(state="visible", timeout=60000)
    reservation_button.click()

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except:
        pass


# ============================================================
# 코트 진입 (텍스트로 정확히 찾기)
# ============================================================

def enter_court_by_number(page, court_no):

    target_text = f"{court_no}번 코트"

    headings = page.locator("h3", has_text=target_text)

    match = None

    for i in range(headings.count()):
        if headings.nth(i).inner_text().strip() == target_text:
            match = headings.nth(i)
            break

    if match is None:
        raise Exception(f"목록에서 '{target_text}' 카드를 찾지 못했습니다.")

    card = match.locator("xpath=ancestor::div[contains(@class,'group')][1]")
    card.get_by_role("button", name="예약").click()

    # 상세 화면 제목이 뜰 때까지 대기 (고정 sleep 대신 조건 대기)
    page.locator("h2", has_text=target_text).first.wait_for(
        state="visible", timeout=15000
    )


def go_back_to_court_list(page):

    page.get_by_label("목록으로").click()

    page.locator("h3", has_text="번 코트").first.wait_for(
        state="visible", timeout=15000
    )


# ============================================================
# 달력: 현재 년/월 읽기, 다음 달로 이동
# ============================================================

def get_current_calendar_month(page):

    buttons = page.locator("button")

    for i in range(buttons.count()):

        try:
            text = buttons.nth(i).inner_text().strip()
        except:
            continue

        if "년" in text and "월" in text:

            match = re.search(r"(\d{4})년\s*(\d{1,2})월", text)

            if match:
                return int(match.group(1)), int(match.group(2))

    return None


def move_to_next_month(page):

    before = get_current_calendar_month(page)

    page.get_by_label("다음 달").click()

    # 표시된 년/월 텍스트가 실제로 바뀔 때까지 대기 (최대 5초, 200ms 간격)
    for _ in range(25):

        current = get_current_calendar_month(page)

        if current and current != before:
            return

        page.wait_for_timeout(200)


# ============================================================
# 달력에서 "뱃지 있는 날짜"만 골라내기
# (뱃지 없음 = 아직 오픈 안 됨, 0/8 = 완전 마감 → 둘 다 건너뜀)
# 평일/주말 여부는 여기서 같이 판별해서 반환한다.
# ============================================================

def get_candidate_dates(page):

    candidates = []  # [(date_key, [체크할 시간대들]), ...]

    date_buttons = page.locator("button[data-date-key]")

    for i in range(date_buttons.count()):

        btn = date_buttons.nth(i)

        date_key = btn.get_attribute("data-date-key")

        if not date_key:
            continue

        try:
            y, m, d = [int(x) for x in date_key.split("-")]
            weekday = date(y, m, d).weekday()  # 월=0 ... 토=5, 일=6
        except:
            continue

        target_times = (
            WEEKEND_TIMES if weekday in (5, 6) else WEEKDAY_TIMES
        )

        # 날짜 버튼 안에는 span이 2개 있음: 날짜 숫자용, 뱃지("4/8")용.
        # 뱃지에만 title 속성("4/8 예약 가능")이 있어서 그걸로 정확히 구분한다.
        badge = btn.locator("span[title]")

        if badge.count() == 0:
            continue

        try:
            badge_text = badge.first.inner_text().strip()
        except:
            continue

        match = re.match(r"(\d+)/(\d+)", badge_text)

        if not match:
            continue

        available_count = int(match.group(1))

        if available_count <= 0:
            continue

        candidates.append((date_key, target_times))

    return candidates


# ============================================================
# 특정 날짜 클릭 후, 원하는 시간대들이 열려있는지 확인
# ============================================================

def click_date(page, date_key):

    page.locator(f'button[data-date-key="{date_key}"]').first.click()

    # 이 사이트는 날짜 전환 시 별도 네트워크 요청이 없어서
    # networkidle 대기만으로는 화면(Vue) 갱신 전에 읽어버릴 수 있음.
    # cart_bot.py에서 검증된 0.4초 고정 대기를 같이 넣어준다.
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except:
        pass

    page.wait_for_timeout(400)


def check_time_slots(page, court_no, date_key, target_times):

    found = []

    buttons = page.locator("button")

    for i in range(buttons.count()):

        btn = buttons.nth(i)

        try:
            # 화면에 실제로 안 보이는 허니팟(미끼) 버튼은 제외
            if btn.get_attribute("aria-hidden") == "true":
                continue

            text = btn.inner_text().replace("\n", " ").strip()

        except:
            continue

        for target_time in target_times:

            if target_time not in text:
                continue

            is_disabled = btn.get_attribute("disabled") is not None

            if not is_disabled:
                found.append((court_no, date_key, target_time))

    return found


# ============================================================
# 코트 하나 전체 확인 (앞으로 MONTHS_TO_CHECK개월)
# ============================================================

def check_court(page, court_no):

    print(f"[{court_no}번 코트] 확인 중...")

    # 코트 넘어갈 때 팝업이 다시 뜨는 경우가 있어, 진입 전마다 확인
    wait_and_close_popups(page, timeout=3000)

    enter_court_by_number(page, court_no)

    found_all = []

    for month_idx in range(MONTHS_TO_CHECK):

        if month_idx > 0:
            move_to_next_month(page)

        candidates = get_candidate_dates(page)

        print(
            f"  ({month_idx + 1}번째 달) 확인 대상 날짜 "
            f"{len(candidates)}개: "
            f"{[d for d, _ in candidates]}"
        )

        for date_key, target_times in candidates:

            click_date(page, date_key)

            found = check_time_slots(page, court_no, date_key, target_times)

            found_all.extend(found)

    go_back_to_court_list(page)

    return found_all


# ============================================================
# 전체 순회 + 텔레그램 알림
# ============================================================

def run_once(page):

    open_reservation_page(page)

    all_found = []

    for court_no in TARGET_COURTS:

        try:
            found = check_court(page, court_no)
            all_found.extend(found)

        except Exception as e:
            print(f"[{court_no}번 코트] 오류: {e}")

            # 오류가 나도 목록 화면으로 복귀를 시도해서 다음 코트는 계속 진행
            try:
                go_back_to_court_list(page)
            except:
                pass

    new_items = [
        item for item in all_found
        if item not in already_notified
    ]

    if new_items:

        lines = ["🎾 빈자리 발견!"]

        for court_no, date_key, target_time in new_items:
            lines.append(f"- {court_no}번 코트 {date_key} {target_time}")

        message = "\n".join(lines)

        print(message)

        send_message(message)

        already_notified.update(new_items)

        save_notified(already_notified)

    else:
        print("새로운 빈자리 없음.")


# ============================================================
# 메인 루프
# ============================================================

def main():

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        if RUN_ONCE:
            # GitHub Actions: 한 번만 확인하고 종료
            try:
                run_once(page)
            except Exception as e:
                print(f"전체 실행 중 오류: {e}")

            browser.close()
            return

        # 로컬 PC: 계속 반복 실행
        while True:

            try:
                run_once(page)

            except Exception as e:
                print(f"전체 실행 중 오류: {e}")

            print(f"{CHECK_INTERVAL_SECONDS}초 대기...")
            time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
