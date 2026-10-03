"""Manual CDP/mouse test against the local test.html page.

Launch from the application's coordinate test button. A missing test.html tab
is opened in dedicated Chrome and its WebSocket URL is discovered automatically.
Keep the test tab foreground, unobscured, and at the same window position.
Press Enter using the keyboard without moving the cursor from the requested
point. Chrome's devicePixelRatio accounts for display scale and desktop zoom.

Run with --workflow-checks for offline registration and queue regression checks.
That mode uses offscreen Qt windows, local HTML video fixtures, and mocked
LMS/mouse access.

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


def find_test_websocket(endpoint, page_name="test.html"):
    """Discover exactly one requested local page using read-only CDP metadata."""
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
    expected = QUrl.fromLocalFile(str(Path(__file__).resolve().with_name(page_name)))
    matches = [tab for tab in tabs if isinstance(tab, dict)
               and tab.get("type") == "page" and QUrl(tab.get("url", "")) == expected]
    if not matches:
        raise TestPageNotOpen(f"전용 Chrome에 {page_name} 페이지가 열려 있지 않습니다.")
    if len(matches) > 1:
        raise RuntimeError(f"{page_name} 페이지가 여러 개 열려 있습니다. 해당 탭을 하나만 남기고 다시 실행하세요.")
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
    """Exercise registration, queue transitions, and local HTML without real input."""
    import os
    import unittest
    from types import SimpleNamespace
    from unittest.mock import Mock, patch

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QEvent, QEventLoop, QTimer
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from browser_reader import ClickedElement, ElementIdentity, ElementPosition, PageSnapshot, PlayerTarget
    from ui import MainWindow
    from video_state import VideoState, read_video_state
    import main as workflow
    import computer_control as desktop

    app = QApplication.instance() or QApplication([])

    class WindowLayoutChecks(unittest.TestCase):
        def setUp(self):
            self.gui = Mock()
            self.gui.IsWindow.return_value = True
            self.gui.IsWindowVisible.return_value = True
            self.gui.IsIconic.return_value = False
            self.gui.IsZoomed.return_value = False
            self.gui.GetClassName.return_value = 'Chrome_WidgetWin_1'
            self.gui.GetWindowText.return_value = 'CatchCatch 좌표 보정 - Google Chrome'
            self.gui.EnumWindows.side_effect = lambda callback, data: callback(22, data)
            self.api = Mock()
            # Odd width and a taskbar at the top: never assume a 1920x1080 desktop.
            self.api.GetMonitorInfo.return_value = {'Work': (0, 40, 1919, 1080)}
            self.constants = SimpleNamespace(
                MONITOR_DEFAULTTOPRIMARY=1, SW_RESTORE=9, HWND_TOP=0,
                SWP_NOACTIVATE=16, SWP_SHOWWINDOW=64)
            class NativeError(Exception):
                pass
            self.modules = patch.dict(sys.modules, {
                'win32api': self.api, 'win32gui': self.gui,
                'win32con': self.constants, 'pywintypes': SimpleNamespace(error=NativeError),
            })
            self.modules.start()
            self.platform = patch.object(desktop.sys, 'platform', 'win32')
            self.platform.start()
            self.addCleanup(self.modules.stop)
            self.addCleanup(self.platform.stop)

        def test_split_excludes_taskbar_and_waits_for_two_matching_observations(self):
            layout = desktop.WindowLayout(11, 'CatchCatch 좌표 보정')
            layout.start()
            self.gui.SetWindowPos.assert_any_call(11, 0, 959, 40, 960, 1040, 80)
            self.gui.SetWindowPos.assert_any_call(22, 0, 0, 40, 959, 1040, 80)
            bounds = {11: (959, 40, 1919, 1080), 22: (0, 40, 959, 1080)}
            self.gui.GetWindowRect.side_effect = lambda handle: bounds[handle]
            self.assertFalse(layout.ready())
            self.assertTrue(layout.ready())

        def test_wrong_or_maximized_bounds_never_become_ready(self):
            layout = desktop.WindowLayout(11)
            self.gui.GetWindowRect.return_value = (0, 0, 1919, 1080)
            self.assertFalse(layout.ready())
            self.assertFalse(layout.ready())
            self.gui.GetWindowRect.return_value = layout.targets[11]
            self.gui.IsZoomed.return_value = True
            self.assertFalse(layout.ready())

        def test_ambiguous_chrome_window_is_rejected_before_movement(self):
            def enumerate_windows(callback, data):
                callback(22, data)
                callback(33, data)
            self.gui.EnumWindows.side_effect = enumerate_windows
            with self.assertRaises(RuntimeError):
                desktop.WindowLayout(11, 'CatchCatch 좌표 보정')
            self.gui.ShowWindow.assert_not_called()
            self.gui.SetWindowPos.assert_not_called()

        def test_invisible_app_does_not_move_an_unrelated_window(self):
            self.gui.IsWindowVisible.return_value = False
            with self.assertRaises(RuntimeError):
                desktop.WindowLayout(11, 'CatchCatch 좌표 보정')
            self.gui.EnumWindows.assert_not_called()
            self.gui.SetWindowPos.assert_not_called()

    class CalibrationLayoutChecks(unittest.TestCase):
        def setUp(self):
            self.window = MainWindow()
            socket = Mock()
            socket.state.return_value = workflow.QAbstractSocket.SocketState.ConnectedState
            self.connection = SimpleNamespace(
                socket=socket, endpoint='http://127.0.0.1:9222', _existing=False)
            self.inspector = workflow.CursorInspector(self.window, self.connection)
            self.inspector.control = Mock(spec=ComputerControl)
            self.reader = Mock(spec=BrowserReader)
            self.reader.visible_page_title.return_value = 'CatchCatch 좌표 보정'
            self.reference = ElementPosition('button', 100, 100, 200, 80)
            self.reader.find_clickable.return_value = self.reference
            self.snapshot = PageSnapshot(
                'file:///start.html', True, 0, 40, 959, 1040, 939, 940, 1.25)
            self.reader.page_snapshot.return_value = self.snapshot
            self.layout = Mock()
            self.layout.ready.return_value = False
            self.patches = [
                patch.object(workflow, 'find_test_websocket', return_value='ws://test'),
                patch.object(workflow, 'BrowserReader', return_value=self.reader),
                patch.object(workflow, 'WindowLayout', return_value=self.layout),
                patch.object(workflow.time, 'monotonic', return_value=100),
                patch.object(workflow.QTimer, 'singleShot'),
            ]
            self.started = [item.start() for item in self.patches]
            for item in self.patches:
                self.addCleanup(item.stop)
            self.countdown = self.started[-1]
            self.addCleanup(self.window.close)
            self.addCleanup(self.inspector.close)

        def prepare_countdown(self):
            self.inspector.begin_calibration()
            self.layout.ready.return_value = True
            self.inspector._wait_for_layout()
            self.inspector._wait_for_layout()

        def test_no_reference_query_or_countdown_until_native_layout_is_ready(self):
            self.inspector.begin_calibration()
            self.layout.start.assert_called_once_with()
            self.inspector._wait_for_layout()
            self.reader.find_clickable.assert_not_called()
            self.countdown.assert_not_called()
            self.inspector.control.calibrate.assert_not_called()
            self.assertFalse(self.window.calibrate_button.isEnabled())

        def test_loading_title_waits_before_window_placement(self):
            self.reader.visible_page_title.return_value = None
            self.inspector.begin_calibration()
            self.started[2].assert_not_called()
            self.countdown.assert_not_called()
            self.reader.find_clickable.assert_not_called()
            self.assertTrue(self.inspector.page_poll.isActive())
            self.reader.visible_page_title.return_value = 'CatchCatch 좌표 보정'
            self.inspector._find_test_page()
            self.layout.start.assert_called_once_with()
            self.assertFalse(self.inspector.page_poll.isActive())

        def test_countdown_waits_for_stable_web_geometry_after_native_layout(self):
            self.inspector.begin_calibration()
            self.layout.ready.return_value = True
            self.inspector._wait_for_layout()
            self.countdown.assert_not_called()
            resized = PageSnapshot('file:///start.html', True, 0, 40, 959, 1040, 939, 900, 1.25)
            self.reader.page_snapshot.return_value = resized
            self.inspector._wait_for_layout()
            self.countdown.assert_not_called()
            self.inspector._wait_for_layout()
            self.countdown.assert_called_once()
            self.assertEqual(self.countdown.call_args.args[0], 5000)
            self.assertFalse(self.inspector.layout_poll.isActive())
            self.inspector.control.calibrate.assert_not_called()

        def test_timeout_stops_before_calibration_and_enables_retry(self):
            self.inspector.begin_calibration()
            self.inspector.layout_deadline = 99
            self.inspector._wait_for_layout()
            self.countdown.assert_not_called()
            self.inspector.control.calibrate.assert_not_called()
            self.assertIsNone(self.inspector.reader)
            self.assertFalse(self.inspector.layout_poll.isActive())
            self.assertTrue(self.window.calibrate_button.isEnabled())

        def test_window_movement_during_countdown_cancels_calibration(self):
            self.prepare_countdown()
            self.layout.ready.return_value = False
            self.countdown.call_args.args[1]()
            self.inspector.control.calibrate.assert_not_called()
            self.assertIsNone(self.inspector.calibration_geometry)

        def test_changed_display_scale_during_countdown_cancels_calibration(self):
            self.prepare_countdown()
            self.reader.page_snapshot.return_value = PageSnapshot(
                'file:///start.html', True, 0, 40, 959, 1040, 939, 940, 1.5)
            self.countdown.call_args.args[1]()
            self.inspector.control.calibrate.assert_not_called()
            self.assertIsNone(self.inspector.calibration_geometry)

        def test_stable_layout_allows_calibration_after_countdown(self):
            self.prepare_countdown()
            with patch.object(self.inspector, 'update_cursor'):
                self.countdown.call_args.args[1]()
            self.inspector.control.calibrate.assert_called_once_with(200, 140, scale=1.25)
            self.assertEqual(self.inspector.calibration_geometry, self.snapshot.geometry)

        def test_close_invalidates_pending_countdown(self):
            self.prepare_countdown()
            callback = self.countdown.call_args.args[1]
            self.inspector.close()
            callback()
            self.inspector.control.calibrate.assert_not_called()
            self.assertFalse(self.inspector.layout_poll.isActive())

        def test_cancel_during_page_query_does_not_start_countdown(self):
            self.inspector.begin_calibration()
            self.layout.ready.return_value = True
            self.reader.find_clickable.side_effect = lambda unused: self.inspector.close()
            self.inspector._wait_for_layout()
            self.reader.page_snapshot.assert_not_called()
            self.countdown.assert_not_called()

        def test_startup_placement_targets_only_the_gui(self):
            with patch.object(workflow.sys, 'platform', 'win32'):
                workflow.place_application(self.window)
            self.started[2].assert_called_once_with(int(self.window.winId()))
            self.layout.start.assert_called_once_with()

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
            self.player = self.reader  # Real LMS navigation stays in the calibrated tab.
            self.target = PlayerTarget(ElementPosition("div", 100, 100, 600, 400),
                                       "my-video_html5_api", "https://media.example/lecture-1.mp4")
            self.reader.find_player_center.return_value = self.target
            self.control = Mock(spec=ComputerControl)
            self.control.scale = 1
            self.control.offset = (0, 100)
            inspector = SimpleNamespace(
                reader=self.reader, control=self.control, busy=False,
                calibration_geometry=self.list_snapshot.geometry, timer=Mock())
            connection = SimpleNamespace(socket=Mock(), endpoint="http://127.0.0.1:9222")
            self.automation = workflow.LectureAutomation(self.window, connection, inspector)
            self.automation.list_reader = self.reader
            self.automation.list_url = self.list_snapshot.url
            self.automation.list_geometry = self.list_snapshot.geometry
            self.automation.baseline_pages = {"list"}
            self.clock = patch.object(workflow.time, "monotonic", return_value=1000.0)
            self.clock.start()
            self.pages = patch.object(workflow, "browser_pages", return_value={"list": "unused"})
            self.pages.start()
            self.player_controls = patch.object(workflow, "ComputerControl")
            self.player_controls.start()
            self.mouse_position = patch.object(workflow.pyautogui, "position", return_value=(400, 300))
            self.mouse_position.start()
            self.video_state = patch.object(workflow, "read_video_state",
                                            return_value=VideoState(60, 0, True, False, 4))
            self.video_state.start()

        def tearDown(self):
            self.automation.stop()
            self.clock.stop()
            self.pages.stop()
            self.player_controls.stop()
            self.mouse_position.stop()
            self.video_state.stop()
            self.window.close()

        @staticmethod
        def identity(number):
            return ElementIdentity(f"#lecture-{number}", "button", f"강의 {number}", (), "")

        def lecture(self, number, duration=60.0, navigation="same"):
            return workflow.RegisteredLecture(
                self.identity(number), self.list_snapshot.url, self.list_snapshot.geometry,
                self.player_snapshot.url, self.player_snapshot.geometry, self.control,
                self.target.identity, duration, navigation)

        def pending_metadata(self, number, duration):
            self.automation.pending = self.identity(number)
            self.automation.player_reader = self.player
            self.automation.deadline = 1030.0
            self.reader.page_snapshot.return_value = self.player_snapshot
            workflow.read_video_state.return_value = VideoState(duration, 0, True, False, 4)
            self.automation._set_state("pending_player")

        def test_registration_preserves_order_and_three_minute_margin(self):
            self.pending_metadata(1, 60.5)
            self.automation.tick()
            self.pending_metadata(2, 90.0)
            self.automation.tick()
            entries = list(self.automation.queue)
            self.assertEqual([entry.identity for entry in entries], [self.identity(1), self.identity(2)])
            self.assertEqual([entry.wait_seconds for entry in entries], [240.5, 270.0])
            self.assertEqual(entries[0].video_identity, self.target.identity)
            self.assertIs(entries[0].player_control, self.control)
            workflow.pyautogui.position.assert_not_called()
            workflow.ComputerControl.assert_not_called()
            self.control.calibrate.assert_not_called()
            self.control.click.assert_not_called()
            self.assertEqual(self.window.queue_list.count(), 2)

        def test_no_selected_lecture_does_not_register(self):
            self.automation._set_state("registering")
            self.automation.tick()
            self.assertFalse(self.automation.queue)
            self.reader.find_player_center.assert_not_called()

        def test_metadata_loading_preserves_selection_then_registers_once_without_space(self):
            self.pending_metadata(1, 60)
            workflow.read_video_state.return_value = None
            self.automation.tick()
            self.assertEqual(self.automation.pending, self.identity(1))
            self.assertEqual(self.automation.state, "pending_player")
            self.assertFalse(self.automation.queue)
            workflow.read_video_state.return_value = VideoState(60, 0, True, False, 1)
            self.automation.tick()
            self.automation.tick()
            self.assertEqual(len(self.automation.queue), 1)
            self.assertIsNone(self.automation.pending)
            self.assertEqual(self.automation.state, "waiting_list")
            workflow.pyautogui.position.assert_not_called()
            self.control.click.assert_not_called()

        def test_click_waits_for_player_page_and_metadata(self):
            clicked = ClickedElement(self.identity(1), ElementPosition("button", 0, 0, 80, 30))
            self.automation._set_state("registering")
            self.automation._begin_player_registration(self.automation.generation, clicked)
            self.automation.tick()  # Still on the list.
            self.assertEqual(self.automation.pending, self.identity(1))
            self.reader.page_snapshot.return_value = self.player_snapshot
            workflow.read_video_state.return_value = None
            self.automation.tick()
            self.assertFalse(self.automation.queue)
            workflow.read_video_state.return_value = VideoState(60, 0, True, False, 1)
            self.automation.tick()
            self.assertEqual(len(self.automation.queue), 1)
            self.assertEqual(self.automation.queue[0].identity, self.identity(1))

        def test_reselecting_registered_lecture_does_not_duplicate(self):
            self.pending_metadata(1, 60)
            self.automation.tick()
            clicked = ClickedElement(self.identity(1), ElementPosition("button", 0, 0, 80, 30))
            self.automation._begin_player_registration(self.automation.generation, clicked)
            self.automation.tick()
            self.assertEqual(len(self.automation.queue), 1)
            self.assertEqual(self.automation.state, "waiting_list")

        def test_metadata_loading_timeout_stops_without_registration(self):
            self.pending_metadata(1, 60)
            workflow.read_video_state.return_value = None
            self.automation.deadline = 999
            self.automation.tick()
            self.assertEqual(self.automation.state, "stopped")
            self.assertFalse(self.automation.queue)
            self.control.click.assert_not_called()

        def test_obscured_player_waits_without_registration(self):
            self.pending_metadata(1, 60)
            self.reader.find_player_center.return_value = None
            self.automation.tick()
            self.assertFalse(self.automation.queue)
            self.assertEqual(self.automation.state, "pending_player")
            self.control.click.assert_not_called()

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

        def test_click_received_during_destroyed_context_preserves_selection(self):
            clicked = ClickedElement(self.identity(1), ElementPosition("button", 0, 0, 80, 30))
            self.reader.take_clicked_identity.side_effect = [None, clicked]
            self.reader.page_snapshot.side_effect = BrowserReaderError('Execution context was destroyed')
            self.reader.stop_click_observation.side_effect = BrowserReaderError('Execution context was destroyed')
            self.automation._set_state("registering")
            self.automation.tick()
            self.assertEqual(self.automation.pending, self.identity(1))
            self.assertEqual(self.automation.state, "pending_player")
            self.assertFalse(self.automation.queue)
            self.control.click.assert_not_called()

        def test_navigation_context_retries_only_until_registration_deadline(self):
            self.pending_metadata(1, 60)
            self.reader.page_snapshot.side_effect = BrowserReaderError('Cannot find context with specified id')
            self.automation.tick()
            self.assertEqual(self.automation.state, "pending_player")
            self.assertEqual(self.automation.pending, self.identity(1))
            self.automation.deadline = 999
            self.automation.tick()
            self.assertEqual(self.automation.state, "stopped")
            self.assertFalse(self.automation.queue)
            self.control.click.assert_not_called()

        def test_return_to_list_rearms_selection_and_finish_starts_playback(self):
            self.pending_metadata(1, 60)
            self.automation.tick()
            self.reader.page_snapshot.return_value = self.list_snapshot
            self.automation.tick()
            self.assertEqual(self.automation.state, "registering")
            self.assertIsNone(self.automation.player_reader)
            self.reader.start_click_observation.assert_called_once_with()
            self.automation.finish_registration()
            self.assertEqual(self.automation.state, "playing")
            self.assertEqual(self.automation.phase, "list")
            self.assertEqual(len(self.automation.queue), 1)
            self.control.click.assert_not_called()
            self.assertNotIn('Space', self.window.registration_instructions.text())

        def test_cdp_rejection_retains_navigation_reason(self):
            socket = Mock()
            socket.state.return_value = workflow.QAbstractSocket.SocketState.ConnectedState
            reader = SimpleNamespace(_socket=socket, _request_id=0, _exchange=Mock(return_value={
                'error': {'code': -32000, 'message': 'Cannot find default execution context'},
            }))
            with self.assertRaises(BrowserReaderError) as caught:
                BrowserReader._command(reader, 'Runtime.evaluate')
            self.assertTrue(caught.exception.during_navigation)

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
                    self.pending_metadata(1, duration)
                    self.automation.tick()
                    self.assertFalse(self.automation.queue)
                    self.assertEqual(self.automation.state, "stopped")
            workflow.ComputerControl.assert_not_called()

        def test_unfocused_player_is_rejected_before_queue_append(self):
            self.pending_metadata(1, 60)
            self.player.page_snapshot.return_value = PageSnapshot(
                self.player_snapshot.url, False, 0, 0, 1200, 900, 1180, 800, 1)
            self.automation.tick()
            self.assertFalse(self.automation.queue)
            self.assertEqual(self.automation.state, "pending_player")
            workflow.ComputerControl.assert_not_called()

        def test_unmatched_list_item_stops_without_click(self):
            self.automation.queue.append(self.lecture(1))
            self.reader.find_registered_clickable.return_value = None
            self.reader.scroll_registered_into_view.return_value = False
            self.automation._begin_playback()
            self.automation.tick()
            self.control.click.assert_not_called()
            self.assertEqual(self.automation.state, "stopped")
            self.reader.scroll_registered_into_view.assert_called_once_with(self.identity(1))

        def test_scrolled_lecture_uses_fresh_coordinates(self):
            self.automation.queue.append(self.lecture(1))
            fresh = ElementPosition("button", 300, 450, 80, 30, "강의 1")
            self.reader.find_registered_clickable.side_effect = [None, fresh, fresh]
            self.reader.scroll_registered_into_view.return_value = True
            self.automation._begin_playback()
            self.automation.tick()
            self.reader.scroll_registered_into_view.assert_called_once_with(self.identity(1))
            self.control.click.assert_not_called()
            self.assertEqual(self.automation.phase, "locate")
            self.automation.tick()
            self.control.click.assert_called_once_with(*fresh.center)
            self.assertEqual(self.automation.phase, "opening")

        def test_obscured_item_after_scroll_times_out_without_click(self):
            self.automation.queue.append(self.lecture(1))
            self.reader.find_registered_clickable.return_value = None
            self.reader.scroll_registered_into_view.return_value = True
            self.automation._begin_playback()
            self.automation.tick()
            self.automation.tick()
            self.automation.deadline = 999
            self.automation.tick()
            self.reader.scroll_registered_into_view.assert_called_once_with(self.identity(1))
            self.control.click.assert_not_called()
            self.assertEqual(self.automation.state, "stopped")
            self.assertEqual(len(self.automation.queue), 1)

        def test_list_item_uses_fresh_coordinates(self):
            self.automation.queue.append(self.lecture(1))
            fresh = ElementPosition("button", 300, 450, 80, 30, "강의 1")
            self.reader.find_registered_clickable.return_value = fresh
            self.automation._begin_playback()
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

        def test_stop_during_player_query_prevents_queue_append(self):
            self.pending_metadata(1, 60)

            def stop_during_query():
                self.automation.stop()
                return self.target

            self.player.find_player_center.side_effect = stop_during_query
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
            self.reader.page_snapshot.return_value = self.player_snapshot
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
            self.reader.page_snapshot.return_value = self.player_snapshot
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

        def test_new_window_is_deferred_without_registration_or_input(self):
            self.automation.pending = self.identity(1)
            self.automation._set_state("pending_player")
            workflow.browser_pages.return_value = {"list": "unused", "new": "unused"}
            self.automation.tick()
            self.assertEqual(self.automation.state, "stopped")
            self.assertFalse(self.automation.queue)
            self.control.click.assert_not_called()
            self.control.go_back.assert_not_called()
            self.control.calibrate.assert_not_called()

        def prepare_video_start(self):
            self.automation.queue.append(self.lecture(1))
            self.reader.page_snapshot.return_value = self.player_snapshot
            self.automation.player_reader = self.reader
            self.automation._set_state("playing")
            self.automation.phase = "opening"
            self.automation.deadline = 1030

        def test_playback_requeries_current_center_instead_of_registration_coordinates(self):
            self.prepare_video_start()
            moved = PlayerTarget(ElementPosition("div", 250, 150, 500, 300),
                                 self.target.video_id, self.target.source)
            self.reader.find_player_center.return_value = moved
            self.automation.tick()
            self.control.click.assert_called_once_with(500, 300)
            self.assertEqual(self.reader.find_player_center.call_count, 2)
            self.assertEqual(self.automation.phase, "starting")

        def test_already_playing_does_not_click(self):
            self.prepare_video_start()
            workflow.read_video_state.return_value = VideoState(60, 0, False, False, 4)
            self.automation.tick()
            self.control.click.assert_not_called()
            self.assertEqual(self.automation.phase, "watching")
            self.assertEqual(self.automation.wait_until, 1240)

        def test_playback_starting_during_last_query_does_not_click(self):
            self.prepare_video_start()
            workflow.read_video_state.side_effect = [VideoState(60, 0, True, False, 4),
                                                     VideoState(60, 0, False, False, 4)]
            self.automation.tick()
            self.control.click.assert_not_called()
            self.assertEqual(self.automation.phase, "watching")

        def test_uncertain_video_targets_stop_without_input(self):
            for target in (None, PlayerTarget(self.target.position, self.target.video_id,
                                             "https://media.example/other.mp4")):
                with self.subTest(target=target):
                    self.automation.queue.clear()
                    self.prepare_video_start()
                    self.reader.find_player_center.return_value = target
                    self.automation.tick()
                    self.control.click.assert_not_called()
                    self.assertEqual(self.automation.state, "stopped")
                    self.assertEqual(len(self.automation.queue), 1)

        def test_center_changing_before_click_stops_without_input(self):
            self.prepare_video_start()
            self.reader.find_player_center.side_effect = [self.target, PlayerTarget(
                ElementPosition("div", 200, 100, 600, 400), self.target.video_id, self.target.source)]
            self.automation.tick()
            self.control.click.assert_not_called()
            self.assertEqual(self.automation.state, "stopped")

        def test_calibration_invalid_during_registration_does_not_register(self):
            self.pending_metadata(1, 60)
            self.control.scale = 2
            self.automation.tick()
            self.assertFalse(self.automation.queue)
            self.control.click.assert_not_called()
            self.assertEqual(self.automation.state, "stopped")

        def test_calibration_invalid_during_playback_stops_before_click(self):
            self.prepare_video_start()
            self.automation.inspector.calibration_geometry = None
            self.automation.tick()
            self.control.click.assert_not_called()
            self.assertEqual(self.automation.state, "stopped")

        def test_focus_lost_before_click_stops_without_input(self):
            self.prepare_video_start()
            unfocused = PageSnapshot(self.player_snapshot.url, False, 0, 0, 1200, 900, 1180, 800, 1)
            self.reader.page_snapshot.side_effect = [self.player_snapshot, unfocused]
            self.automation.tick()
            self.control.click.assert_not_called()
            self.assertEqual(self.automation.state, "stopped")

        def test_wrong_player_page_stops_without_input(self):
            self.prepare_video_start()
            self.reader.page_snapshot.return_value = self.list_snapshot
            self.automation.tick()
            self.control.click.assert_not_called()
            self.assertEqual(self.automation.state, "stopped")

        def test_stop_during_final_center_query_prevents_click(self):
            self.prepare_video_start()
            def query():
                self.automation.stop()
                return self.target
            self.reader.find_player_center.side_effect = query
            self.automation.tick()
            self.control.click.assert_not_called()
            self.assertEqual(self.automation.state, "stopped")

    class VideoElementChecks(unittest.TestCase):
        """Execute the production JavaScript on a local HTML player fixture."""

        def setUp(self):
            from collections import deque

            self.view = QWebEngineView()
            self.view.resize(1200, 800)
            self.view.show()
            self.page = self.view.page()

            def load(callback):
                self.page.loadFinished.connect(callback)
                self.page.setHtml('''<!doctype html><html><head><style>
                    #my-video {position: absolute; left: 100px; top: 100px;
                               width: 600px; height: 400px;}
                    video {width: 100%; height: 100%;}
                    #play {position: absolute; left: 280px; top: 180px;
                           width: 80px; height: 40px;}
                </style></head><body>
                    <div id="my-video" class="video-js">
                        <video id="my-video_html5_api"></video><button id="play">Play</button>
                    </div>
                </body></html>''')

            self.assertTrue(self.wait_for_result(load))
            self.page.loadFinished.disconnect()
            self.evaluate('''
                var video = document.querySelector('video');
                var player = document.querySelector('#my-video');
                Object.defineProperties(video, {
                    duration: {value: 60.5, configurable: true},
                    currentTime: {value: 10, configurable: true},
                    paused: {value: true, configurable: true},
                    ended: {value: false, configurable: true},
                    readyState: {value: 4, configurable: true},
                    currentSrc: {value: 'https://media.example/lecture-1.mp4', configurable: true}
                });
                true;
            ''')
            self.reader = SimpleNamespace(
                _evaluate=self.evaluate, _binding_installed=True, _observation_mode=None,
                _observation_error=None, _clicks=deque(),
                _binding_name='_test_capture', _listener_name='_test_listeners')

        def tearDown(self):
            self.view.close()
            self.view.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()

        def wait_for_result(self, start):
            loop, timer, values = QEventLoop(), QTimer(), []
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)

            def received(value):
                values.append(value)
                loop.quit()

            timer.start(5000)
            start(received)
            if not values:
                loop.exec()
            timer.stop()
            self.assertTrue(values, 'Local player fixture timed out')
            return values[0]

        def evaluate(self, expression):
            script = ('(() => { try { return JSON.stringify({value: (0, eval)(' +
                      json.dumps(expression) + ')}); } catch (error) {'
                      'return JSON.stringify({error: error.toString()}); } })()')
            result = self.wait_for_result(lambda callback: self.page.runJavaScript(script, callback))
            response = json.loads(result)
            if 'error' in response:
                raise BrowserReaderError(response['error'])
            return response.get('value')

        def test_wrapped_video_state_reads_actual_video(self):
            self.assertEqual(read_video_state(self.reader), VideoState(60.5, 10, True, False, 4))

        def test_direct_video_state_remains_supported(self):
            self.evaluate("player.replaceWith(video); video.id = 'my-video'; true;")
            self.assertEqual(read_video_state(self.reader), VideoState(60.5, 10, True, False, 4))

        def test_missing_player_waits(self):
            self.evaluate('player.remove(); true;')
            self.assertIsNone(read_video_state(self.reader))

        def test_empty_player_waits(self):
            self.evaluate('video.remove(); true;')
            self.assertIsNone(read_video_state(self.reader))

        def test_loading_video_waits(self):
            self.evaluate('Object.defineProperty(video, "duration", {value: NaN}); true;')
            self.assertIsNone(read_video_state(self.reader))

        def test_metadata_not_ready_waits_even_with_finite_duration(self):
            self.evaluate('Object.defineProperty(video, "readyState", {value: 0}); true;')
            self.assertIsNone(read_video_state(self.reader))

        def test_metadata_ready_allows_registration_before_playback_data(self):
            self.evaluate('Object.defineProperty(video, "readyState", {value: 1}); true;')
            self.assertEqual(read_video_state(self.reader), VideoState(60.5, 10, True, False, 1))

        def test_duplicate_player_is_rejected(self):
            self.evaluate('document.body.appendChild(player.cloneNode(true)); true;')
            with self.assertRaisesRegex(BrowserReaderError, 'Ambiguous'):
                read_video_state(self.reader)

        def test_multiple_inner_videos_are_rejected(self):
            self.evaluate('player.appendChild(video.cloneNode()); true;')
            with self.assertRaisesRegex(BrowserReaderError, 'Ambiguous'):
                read_video_state(self.reader)

        def test_player_center_comes_from_container_and_actual_video_identity(self):
            target = BrowserReader.find_player_center(self.reader)
            self.assertEqual(target.position.center, (400, 300))
            self.assertEqual(target.identity,
                             ('my-video_html5_api', 'https://media.example/lecture-1.mp4'))

        def test_player_center_moves_with_dom(self):
            self.evaluate("player.style.left = '200px'; true;")
            self.assertEqual(BrowserReader.find_player_center(self.reader).position.center, (500, 300))

        def test_player_center_rejects_unrelated_overlay(self):
            self.evaluate('''
                var cover = document.createElement('div');
                cover.style = 'position: fixed; inset: 0; z-index: 100;';
                document.body.appendChild(cover);
                true;
            ''')
            self.assertIsNone(BrowserReader.find_player_center(self.reader))

        def test_player_center_rejects_unknown_source(self):
            self.evaluate('Object.defineProperty(video, "currentSrc", {value: ""}); true;')
            self.assertIsNone(BrowserReader.find_player_center(self.reader))

        def test_player_center_rejects_hidden_player(self):
            self.evaluate('player.style.opacity = "0"; true;')
            self.assertIsNone(BrowserReader.find_player_center(self.reader))

        def test_player_center_rejects_outside_viewport(self):
            self.evaluate('player.style.left = "-700px"; true;')
            self.assertIsNone(BrowserReader.find_player_center(self.reader))

        def test_player_center_rejects_multiple_videos(self):
            self.evaluate('player.appendChild(video.cloneNode()); true;')
            with self.assertRaisesRegex(BrowserReaderError, 'Ambiguous'):
                BrowserReader.find_player_center(self.reader)

        def test_player_center_waits_for_metadata(self):
            self.evaluate('Object.defineProperty(video, "readyState", {value: 0}); true;')
            self.assertIsNone(BrowserReader.find_player_center(self.reader))

        def test_cdp_scroll_requires_matching_identity_and_does_not_click(self):
            self.evaluate('''
                var lecture = document.createElement('button');
                lecture.id = 'lecture-scroll'; lecture.textContent = '강의';
                lecture.style = 'position: absolute; top: 1800px; left: 100px;';
                var clicks = 0;
                lecture.addEventListener('click', () => clicks++);
                document.body.appendChild(lecture);
                true;
            ''')
            wrong = ElementIdentity('#lecture-scroll', 'button', '다른 강의',
                                    (('id', 'lecture-scroll'),), '')
            self.assertFalse(BrowserReader.scroll_registered_into_view(self.reader, wrong))
            self.assertEqual(self.evaluate('window.scrollY'), 0)
            expected = ElementIdentity('#lecture-scroll', 'button', '강의',
                                       (('id', 'lecture-scroll'),), '')
            self.assertTrue(BrowserReader.scroll_registered_into_view(self.reader, expected))
            self.assertGreater(self.evaluate('window.scrollY'), 0)
            self.assertTrue(self.evaluate('''(() => {
                const rect = lecture.getBoundingClientRect();
                return rect.top >= 0 && rect.bottom <= innerHeight;
            })()'''))
            self.assertEqual(self.evaluate('clicks'), 0)

        def test_registration_observer_only_watches_list_clicks_without_space(self):
            self.evaluate('''
                var callbacks = {}, captures = [];
                document.hasFocus = () => true;
                document.addEventListener = (kind, listener) => { callbacks[kind] = listener; };
                window._test_capture = value => captures.push(JSON.parse(value));
                true;
            ''')
            BrowserReader._start_observation(self.reader, 'click')
            self.assertEqual(self.evaluate('Object.keys(callbacks)'), ['click'])
            self.assertFalse(hasattr(BrowserReader, 'start_video_observation'))
            self.assertFalse(hasattr(BrowserReader, 'take_video_capture'))
            self.evaluate("callbacks.click({isTrusted: true, button: 0, target: document.querySelector('#play')}); true;")
            captures = self.evaluate('captures')
            self.assertEqual(len(captures), 1)
            self.assertEqual(captures[0]['mode'], 'click')
            self.assertEqual(captures[0]['identity']['selector'], '#play')

    suite = unittest.TestSuite([
        unittest.defaultTestLoader.loadTestsFromTestCase(WindowLayoutChecks),
        unittest.defaultTestLoader.loadTestsFromTestCase(CalibrationLayoutChecks),
        unittest.defaultTestLoader.loadTestsFromTestCase(WorkflowChecks),
        unittest.defaultTestLoader.loadTestsFromTestCase(VideoElementChecks),
    ])
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
