"""Manual CDP/mouse test against the local test.html page.

Launch from the application's coordinate test button. A missing test.html tab
is opened in dedicated Chrome and its WebSocket URL is discovered automatically.
Keep the test tab foreground, unobscured, and at the same window position.
Press Enter using the keyboard without moving the cursor from the requested
point. CSS pixels and screen coordinates must use the same scale.

Required buttons in test.html:
    #reference: the upper-left button labeled '보정 기준'.
    #target: the lower-right button labeled '클릭 대상', containing a span.
"""

import time
import argparse
import json
import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

from PySide6.QtCore import QCoreApplication, QUrl

from browser_reader import BrowserReader, BrowserReaderError
from computer_control import ComputerControl


class TestPageNotOpen(RuntimeError):
    """No matching test tab; opening the local page is permitted."""


def find_test_websocket(endpoint):
    """Discover exactly one local test page using read-only CDP metadata."""
    address = urlsplit(endpoint)
    if (address.scheme != "http" or address.hostname != "127.0.0.1"
            or not address.port or address.username or address.password
            or address.path not in ("", "/") or address.query or address.fragment):
        raise ValueError("앱의 localhost CDP 연결 주소가 필요합니다.")
    # Local debugging traffic must not be routed through a system HTTP proxy.
    opener = build_opener(ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{address.port}/json/list", timeout=5) as response:
        if urlsplit(response.geturl()).netloc != address.netloc:
            raise ValueError("CDP 응답 주소가 연결한 브라우저와 다릅니다.")
        tabs = json.load(response)
    if not isinstance(tabs, list):
        raise ValueError("브라우저 탭 목록을 확인할 수 없습니다.")
    expected = QUrl.fromLocalFile(str(Path(__file__).resolve().with_name("test.html")))
    matches = [tab for tab in tabs if isinstance(tab, dict)
               and tab.get("type") == "page" and QUrl(tab.get("url", "")) == expected]
    if not matches:
        raise TestPageNotOpen("전용 Chrome에 테스트 페이지가 열려 있지 않습니다.")
    if len(matches) > 1:
        raise RuntimeError("테스트 페이지가 여러 개 열려 있습니다. test.html 탭을 하나만 남기고 다시 실행하세요.")
    websocket = matches[0].get("webSocketDebuggerUrl", "")
    url = urlsplit(websocket)
    if (url.scheme != "ws" or url.hostname != "127.0.0.1"
            or url.port != address.port or url.username or url.password
            or not url.path.startswith("/devtools/page/") or url.query or url.fragment):
        raise ValueError("테스트 탭의 CDP 연결 주소를 확인할 수 없습니다.")
    return websocket


def prepare_test_page(endpoint, chrome, profile, timeout=10):
    """Reuse a unique test tab or open it via Chrome's normal launch arguments."""
    page = Path(__file__).resolve().with_name("test.html")
    if not page.is_file():
        raise RuntimeError("프로젝트의 test.html 파일을 찾을 수 없습니다.")
    try:
        return find_test_websocket(endpoint)
    except TestPageNotOpen:
        pass
    print("전용 Chrome에서 테스트 페이지를 여는 중입니다…", flush=True)
    subprocess.Popen([
        chrome, f"--user-data-dir={profile}",
        "--new-window", page.as_uri(),
    ])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return find_test_websocket(endpoint)
        except TestPageNotOpen:
            time.sleep(0.25)
    raise RuntimeError("테스트 페이지가 열리지 않았습니다. 전용 Chrome 상태를 확인하고 다시 실행하세요.")


def wait_for_reference(reader, timeout=10):
    """Allow the newly opened page to finish rendering before calibration."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reference = reader.find_clickable("#reference")
        if reference is not None:
            return reference
        time.sleep(0.25)
    raise RuntimeError("test.html의 '보정 기준' 버튼 (#reference)이 준비되지 않았습니다.")


def main():
    parser = argparse.ArgumentParser(description="로컬 test.html 좌표 변환 테스트")
    parser.add_argument("--cdp-endpoint", required=True,
                        help="앱이 자동으로 전달하는 localhost CDP 주소")
    parser.add_argument("--chrome-executable", required=True)
    parser.add_argument("--chrome-profile", required=True)
    args = parser.parse_args()
    app = QCoreApplication([])
    print("Chrome의 test.html 페이지에서 다음 두 버튼을 사용합니다.")
    print("  보정용: 왼쪽 위 '보정 기준' 버튼 (HTML ID: reference)")
    print("  탐색·클릭용: 오른쪽 아래 '클릭 대상' 버튼 (HTML ID: target)")
    print("각 단계에서 마우스 위치를 유지한 채 키보드로 Enter를 누르세요.")
    reader = None

    try:
        reader = BrowserReader(prepare_test_page(
            args.cdp_endpoint, args.chrome_executable, args.chrome_profile))
        control = ComputerControl()
        reader.connect()
        print("테스트 페이지가 보이도록 Chrome 창을 앞에 두세요.", flush=True)
        reference = wait_for_reference(reader)
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
    except (BrowserReaderError, RuntimeError, ValueError, OSError, URLError) as error:
        print("테스트 실패:", error)
        return 1
    finally:
        if reader is not None:
            reader.close()


if __name__ == "__main__":
    raise SystemExit(main())
