"""Manual CDP/mouse test against the local test.html page.

Run with the project's interpreter, then paste the test tab's
webSocketDebuggerUrl from http://127.0.0.1:9222/json/list.
Keep the test tab foreground, unobscured, and at the same window position.
Press Enter using the keyboard without moving the cursor from the requested
point. CSS pixels and screen coordinates must use the same scale.

Required buttons in test.html:
    #reference: the upper-left button labeled '보정 기준'.
    #target: the lower-right button labeled '클릭 대상', containing a span.
"""

import time

from PySide6.QtCore import QCoreApplication

from browser_reader import BrowserReader, BrowserReaderError
from computer_control import ComputerControl


def main():
    app = QCoreApplication([])
    print("Chrome의 test.html 페이지에서 다음 두 버튼을 사용합니다.")
    print("  보정용: 왼쪽 위 '보정 기준' 버튼 (HTML ID: reference)")
    print("  탐색·클릭용: 오른쪽 아래 '클릭 대상' 버튼 (HTML ID: target)")
    print("각 단계에서 마우스 위치를 유지한 채 키보드로 Enter를 누르세요.")
    websocket_url = input("테스트 탭의 webSocketDebuggerUrl: ").strip()
    reader = BrowserReader(websocket_url)
    control = ComputerControl()

    try:
        reader.connect()
        reference = reader.find_clickable("#reference")
        if reference is None:
            raise RuntimeError("test.html의 '보정 기준' 버튼 (#reference)을 찾을 수 없습니다.")
        print("'보정 기준' 버튼의 웹 중심 좌표:", reference.center, "(예상: 200, 140)")

        input("왼쪽 위 '보정 기준' 버튼 (#reference)의 사각형 정중앙에 마우스를 놓고 Enter: ")
        print("오프셋:", control.calibrate(*reference.center))

        input("오른쪽 아래 '클릭 대상' 버튼 (#target) 안의 글자(span) 위에 마우스를 놓고 Enter: ")
        target = control.find_clickable_under_cursor(reader)
        if target is None:
            raise RuntimeError("현재 마우스 아래에 클릭 가능한 요소가 없습니다.")
        expected = reader.find_clickable("#target")
        if expected is None or target != expected:
            raise RuntimeError("마우스 아래 요소가 '클릭 대상' 버튼 (#target)과 일치하지 않습니다.")
        print("탐색 결과:", target)
        print("'클릭 대상' 버튼의 웹 중심 좌표:", target.center, "(예상: 500, 340)")
        print("자동 이동을 볼 수 있도록 마우스를 버튼 밖의 다른 위치로 옮기세요. 3초 뒤 '클릭 대상' 버튼 중앙으로 이동합니다.", flush=True)
        time.sleep(3)
        control.move_to(*target.center)

        answer = input("마우스가 '클릭 대상' 버튼 (#target)의 정중앙에 도착했나요? 클릭하려면 y 입력: ")
        if answer.strip().lower() == "y":
            fresh_target = reader.find_clickable("#target")
            if fresh_target is None or fresh_target != target:
                raise RuntimeError("'클릭 대상' 버튼 (#target)의 위치나 상태가 바뀌었습니다. 테스트를 다시 실행하세요.")
            control.click(*fresh_target.center)
            print("#target 버튼을 클릭했습니다. '클릭 대상' 글자가 '클릭 성공'으로 바뀌었는지 확인하세요.")
        else:
            print("마우스 이동 테스트를 완료했습니다.")
        return 0
    except (BrowserReaderError, RuntimeError, ValueError) as error:
        print("테스트 실패:", error)
        return 1
    finally:
        reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
