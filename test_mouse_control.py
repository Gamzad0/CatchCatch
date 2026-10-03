"""Manual CDP/mouse test against the local test.html page.

Launch from the application's coordinate test button. A missing test.html tab
is opened in dedicated Chrome and its WebSocket URL is discovered automatically.
Keep the test tab foreground, unobscured, and at the same window position.
Press Enter using the keyboard without moving the cursor from the requested
point. Chrome's devicePixelRatio accounts for display scale and desktop zoom.

Run with --workflow-checks for offline registration and queue regression checks.
That mode uses an offscreen Qt window and mocked browser/mouse access.

Required buttons in test.html:
    #reference: the upper-left button labeled '보정 기준'.
    #target: the lower-right button labeled '클릭 대상', containing a span.
"""

import time
import argparse
import json
import math
import subprocess
import sys
import pyautogui
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


def check_calibration_scale(reader, control):
    if not math.isclose(reader.device_pixel_ratio(), control.scale, rel_tol=1e-6):
        control.clear_calibration()
        raise RuntimeError("보정 이후 화면 배율 또는 Chrome 확대율이 바뀌었습니다. 테스트를 다시 실행하세요.")


def run_workflow_checks():
    """Exercise registration and queue transitions without Chrome or real input."""
    import os
    import unittest
    from types import SimpleNamespace
    from unittest.mock import Mock, patch

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from browser_reader import ClickedElement, ElementIdentity, ElementPosition, PageSnapshot, VideoCapture
    from ui import MainWindow
    from video_state import VideoState
    import main as workflow

    app = QApplication.instance() or QApplication([])

    class WorkflowChecks(unittest.TestCase):
        def setUp(self):
            self.window = MainWindow()
            self.list_snapshot = PageSnapshot(
                "https://lms.example/course", True, 0, 0, 1200, 900, 1180, 800, 1)
            self.player_snapshot = PageSnapshot(
                "https://lms.example/player", True, 0, 0, 1200, 900, 1180, 800, 1)
            self.reader = Mock(spec=BrowserReader)
            self.reader.page_snapshot.return_value = self.list_snapshot
            self.reader.take_clicked_identity.return_value = None
            self.player = Mock(spec=BrowserReader)
            self.player.page_snapshot.return_value = self.player_snapshot
            self.player.video_point_is_visible.return_value = True
            self.control = Mock(spec=ComputerControl)
            inspector = SimpleNamespace(
                reader=self.reader, control=self.control, busy=False,
                calibration_geometry=self.list_snapshot.geometry, timer=Mock())
            connection = SimpleNamespace(socket=Mock(), endpoint="http://127.0.0.1:9222")
            self.automation = workflow.LectureAutomation(self.window, connection, inspector)
            self.automation.list_reader = self.reader
            self.automation.list_url = self.list_snapshot.url
            self.automation.list_geometry = self.list_snapshot.geometry
            self.clock = patch.object(workflow.time, "monotonic", return_value=1000.0)
            self.clock.start()
            self.pages = patch.object(workflow, "browser_pages", return_value={"list": "unused"})
            self.pages.start()
            self.player_controls = patch.object(workflow, "ComputerControl")
            self.player_controls.start()
            self.mouse_position = patch.object(workflow.pyautogui, "position", return_value=(400, 300))
            self.mouse_position.start()

        def tearDown(self):
            self.automation.stop()
            self.clock.stop()
            self.pages.stop()
            self.player_controls.stop()
            self.mouse_position.stop()
            self.window.close()

        @staticmethod
        def identity(number):
            return ElementIdentity(f"#lecture-{number}", "button", f"강의 {number}", (), "")

        def lecture(self, number, duration=60.0, navigation="same"):
            return workflow.RegisteredLecture(
                self.identity(number), self.list_snapshot.url, self.list_snapshot.geometry,
                self.player_snapshot.url, self.player_snapshot.geometry, self.control,
                (400.0, 300.0), duration, navigation)

        def pending_capture(self, number, duration):
            self.automation.pending = self.identity(number)
            self.automation.player_reader = self.player
            self.automation.deadline = 1030.0
            self.player.take_video_capture.return_value = VideoCapture(400, 300, duration)
            self.automation._set_state("pending_player")

        def test_registration_preserves_order_and_three_minute_margin(self):
            self.pending_capture(1, 60.5)
            self.automation.tick()
            self.pending_capture(2, 90.0)
            self.automation.tick()
            entries = list(self.automation.queue)
            self.assertEqual([entry.identity for entry in entries], [self.identity(1), self.identity(2)])
            self.assertEqual([entry.wait_seconds for entry in entries], [240.5, 270.0])
            self.assertEqual(entries[0].play_point, (400, 300))
            self.assertEqual(self.window.queue_list.count(), 2)

        def test_space_without_selected_lecture_does_not_register(self):
            self.automation._set_state("registering")
            self.player.take_video_capture.return_value = VideoCapture(400, 300, 60)
            self.automation.tick()
            self.assertFalse(self.automation.queue)
            self.player.take_video_capture.assert_not_called()

        def test_click_received_during_navigation_query_preserves_selection(self):
            clicked = ClickedElement(
                self.identity(1), ElementPosition("button", 300, 450, 80, 30, "강의 1"))
            self.reader.take_clicked_identity.side_effect = [None, clicked]
            self.reader.page_snapshot.return_value = self.player_snapshot
            self.automation._set_state("registering")
            self.automation.tick()
            self.assertEqual(self.automation.state, "pending_player")
            self.assertEqual(self.automation.pending, self.identity(1))
            self.assertFalse(self.automation.queue)
            self.control.click.assert_not_called()

        def test_dom_query_error_preserves_exception_message_without_stack(self):
            reader = SimpleNamespace(_command=Mock(return_value={
                'exceptionDetails': {
                    'text': 'Uncaught',
                    'exception': {
                        'description': 'Error: Expected HTML video\n    at <anonymous>:5:12'
                    },
                },
            }))
            with self.assertRaises(BrowserReaderError) as caught:
                BrowserReader._evaluate(reader, 'unused')
            self.assertEqual(str(caught.exception), 'DOM query failed: Error: Expected HTML video')

        def test_dom_query_error_falls_back_to_cdp_exception_text(self):
            reader = SimpleNamespace(_command=Mock(return_value={
                'exceptionDetails': {'text': 'Execution context was destroyed'},
            }))
            with self.assertRaises(BrowserReaderError) as caught:
                BrowserReader._evaluate(reader, 'unused')
            self.assertIn('Execution context was destroyed', str(caught.exception))

        def test_registration_query_failure_shows_operation_and_stops_before_input(self):
            def page_snapshot():
                raise BrowserReaderError('DOM query failed: ReferenceError: document is not defined')

            self.reader.page_snapshot = page_snapshot
            self.automation._set_state('registering')
            self.automation.tick()
            self.assertEqual(self.automation.state, 'stopped')
            self.assertIn('page_snapshot: DOM query failed: ReferenceError: document is not defined',
                          self.window.playback_status.text())
            self.assertFalse(self.automation.queue)
            self.control.click.assert_not_called()

        def test_invalid_duration_is_rejected_before_queue_append(self):
            for duration in (float("nan"), float("inf"), 0.0, -1.0):
                with self.subTest(duration=duration):
                    self.pending_capture(1, duration)
                    self.automation.tick()
                    self.assertFalse(self.automation.queue)
                    self.assertEqual(self.automation.state, "stopped")
            workflow.ComputerControl.assert_not_called()

        def test_unfocused_player_is_rejected_before_queue_append(self):
            self.pending_capture(1, 60)
            self.player.page_snapshot.return_value = PageSnapshot(
                self.player_snapshot.url, False, 0, 0, 1200, 900, 1180, 800, 1)
            self.automation.tick()
            self.assertFalse(self.automation.queue)
            self.assertEqual(self.automation.state, "stopped")
            workflow.ComputerControl.assert_not_called()

        def test_scrolled_lecture_uses_fresh_coordinates(self):
            self.automation.queue.append(self.lecture(1))
            fresh = ElementPosition("button", 300, 450, 80, 30, "강의 1")
            self.reader.find_registered_clickable.side_effect = [None, fresh, fresh]
            self.reader.scroll_registered_into_view.return_value = True
            self.automation._begin_playback()
            self.automation.tick()
            self.reader.scroll_registered_into_view.assert_called_once_with(self.identity(1))
            self.control.click.assert_not_called()
            self.automation.tick()
            self.control.click.assert_called_once_with(*fresh.center)
            self.assertEqual(self.automation.phase, "opening")

        def test_moved_window_stops_before_input(self):
            item = self.lecture(1)
            self.automation.queue.append(item)
            self.reader.page_snapshot.return_value = PageSnapshot(
                self.list_snapshot.url, True, 20, 0, 1200, 900, 1180, 800, 1)
            self.automation._begin_playback()
            self.automation.tick()
            self.control.click.assert_not_called()
            self.reader.find_registered_clickable.assert_not_called()
            self.assertEqual(self.automation.state, "stopped")
            self.assertEqual(list(self.automation.queue), [item])

        def test_stop_during_element_query_prevents_click(self):
            item = self.lecture(1)
            self.automation.queue.append(item)
            self.automation._begin_playback()

            def stop_during_query(_identity):
                self.automation.stop()
                return ElementPosition("button", 300, 450, 80, 30, "강의 1")

            self.reader.find_registered_clickable.side_effect = stop_during_query
            self.automation.tick()
            self.control.click.assert_not_called()
            self.assertEqual(list(self.automation.queue), [item])
            self.assertEqual(self.automation.state, "stopped")
            self.assertFalse(self.automation.timer.isActive())

        def test_stop_during_capture_prevents_queue_append(self):
            self.pending_capture(1, 60)

            def stop_during_capture():
                self.automation.stop()
                return VideoCapture(400, 300, 60)

            self.player.take_video_capture.side_effect = stop_during_capture
            self.automation.tick()
            self.assertFalse(self.automation.queue)
            self.assertEqual(self.automation.state, "stopped")
            workflow.ComputerControl.assert_not_called()

        def test_confirmed_playback_starts_full_duration_timer(self):
            item = self.lecture(1, 60.5)
            self.automation.queue.append(item)
            self.automation.player_reader = self.player
            self.automation._set_state("playing")
            self.automation.phase = "starting"
            with patch("main.read_video_state", return_value=VideoState(60.5, 0, False, False, 4)):
                self.automation.tick()
            self.assertEqual(self.automation.phase, "watching")
            self.assertEqual(self.automation.wait_until, 1240.5)

        def test_queue_advances_only_after_verified_list_return(self):
            first, second = self.lecture(1), self.lecture(2)
            self.automation.queue.extend([first, second])
            self.automation.player_reader = self.player
            self.automation._set_state("playing")
            self.automation.phase = "watching"
            self.automation.wait_until = 1000.0
            self.automation.tick()
            self.assertEqual(list(self.automation.queue), [first, second])
            self.automation.tick()
            self.control.go_back.assert_called_once_with()
            self.assertEqual(list(self.automation.queue), [first, second])
            self.reader.page_snapshot.return_value = self.player_snapshot
            self.automation.tick()
            self.assertEqual(list(self.automation.queue), [first, second])
            self.reader.page_snapshot.return_value = PageSnapshot(
                self.list_snapshot.url, False, 0, 0, 1200, 900, 1180, 800, 1)
            self.automation.tick()
            self.assertEqual(list(self.automation.queue), [first, second])
            self.reader.page_snapshot.return_value = self.list_snapshot
            self.automation.tick()
            self.assertEqual(list(self.automation.queue), [second])
            self.assertEqual(self.automation.phase, "list")

        def test_popup_return_closes_player_and_preserves_entry_until_list_confirmed(self):
            item = self.lecture(1, navigation="new")
            self.automation.queue.append(item)
            self.automation.player_reader = self.player
            self.automation.owns_player = True
            self.automation._set_state("playing")
            self.automation.phase = "return"
            self.automation.tick()
            self.control.close_player.assert_called_once_with()
            self.control.go_back.assert_not_called()
            self.player.close.assert_not_called()
            self.assertEqual(list(self.automation.queue), [item])
            self.assertEqual(self.automation.phase, "returning")
            self.automation.tick()
            self.player.close.assert_called_once_with()
            self.assertFalse(self.automation.queue)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(WorkflowChecks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    # Keep the application alive until every QObject in the checks is cleaned up.
    app.processEvents()
    return 0 if result.wasSuccessful() else 1


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
        scale = reader.device_pixel_ratio()
        print("웹→화면 배율 (devicePixelRatio):", scale)
        print("오프셋:", control.calibrate(*reference.center, scale=scale))

        input("오른쪽 아래 '클릭 대상' 버튼 (#target) 안의 글자(span) 위에 마우스를 놓고 Enter: ")
        check_calibration_scale(reader, control)
        expected = reader.find_clickable("#target")
        if expected is None:
            raise RuntimeError("'클릭 대상' 버튼 (#target)이 보이지 않거나 가려져 있습니다.")
        screen_point = pyautogui.position()
        web_point = control.screen_to_web(*screen_point)
        print("현재 마우스 화면 좌표:", tuple(screen_point))
        print("변환된 마우스 웹 좌표:", web_point)
        print("클릭 대상의 웹 영역:",
              (expected.x, expected.y, expected.x + expected.width,
               expected.y + expected.height))
        print("클릭 대상의 예상 화면 중심:", control.web_to_screen(*expected.center))
        target = reader.find_clickable_at(*web_point)
        if target is None:
            raise RuntimeError(
                "변환된 마우스 웹 좌표에 클릭 가능한 요소가 없습니다. "
                "출력된 좌표와 대상 영역, 화면 배율을 확인하세요.")
        if target != expected:
            raise RuntimeError("마우스 아래 요소가 '클릭 대상' 버튼 (#target)과 일치하지 않습니다.")
        print("탐색 결과:", target)
        print("'클릭 대상' 버튼의 웹 중심 좌표:", target.center, "(예상: 500, 340)")
        print("자동 이동을 볼 수 있도록 마우스를 버튼 밖의 다른 위치로 옮기세요. 3초 뒤 '클릭 대상' 버튼 중앙으로 이동합니다.", flush=True)
        time.sleep(3)
        check_calibration_scale(reader, control)
        control.move_to(*target.center)

        answer = input("마우스가 '클릭 대상' 버튼 (#target)의 정중앙에 도착했나요? 클릭하려면 y 입력: ")
        if answer.strip().lower() == "y":
            check_calibration_scale(reader, control)
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
    raise SystemExit(run_workflow_checks() if sys.argv[1:] == ["--workflow-checks"] else main())
