import sys
import os
import subprocess
import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

import pyautogui

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import QApplication

from ui import MainWindow
from browser_reader import BrowserReader, BrowserReaderError
from computer_control import ComputerControl
from test_mouse_control import TestPageNotOpen, find_test_websocket
from video_state import read_video_state


class CursorInspector(QObject):
    """Calibrate on the initial test page, then inspect its current tab."""

    def __init__(self, window, connection):
        super().__init__(window)
        self.window = window
        self.connection = connection
        self.reader = None
        self.control = ComputerControl()
        self.reference = None
        self.busy = False
        self.query_failures = 0
        self.calibration_generation = 0
        self.page_attempts = 0
        self.calibration_geometry = None
        self.page_poll = QTimer(self)
        self.page_poll.setInterval(250)
        self.page_poll.timeout.connect(self._find_test_page)
        self.timer = QTimer(self)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.update_cursor)
        window.calibrate_button.clicked.connect(self.begin_calibration)
        connection.socket.connected.connect(self.begin_calibration)
        connection.socket.disconnected.connect(self.close)

    def begin_calibration(self):
        self.close()
        if (not self.connection.endpoint or self.connection.socket.state()
                != QAbstractSocket.SocketState.ConnectedState):
            self.window.cursor_status.setText("먼저 브라우저를 연결하세요.")
            return
        self.window.cursor_status.setText("보정 화면을 준비하는 중입니다…")
        self.page_attempts = 0
        self._find_test_page()

    def _find_test_page(self):
        try:
            websocket = find_test_websocket(self.connection.endpoint)
        except TestPageNotOpen:
            if self.page_attempts == 0 and self.connection._existing:
                page = Path(__file__).resolve().with_name("test.html")
                if not page.is_file():
                    self.window.cursor_status.setText("보정 화면 test.html을 찾을 수 없습니다.")
                    return
                try:
                    subprocess.Popen([
                        str(chrome_executable()),
                        f"--user-data-dir={self.connection.profile}",
                        "--new-window", page.as_uri(),
                    ])
                except OSError as error:
                    self.window.cursor_status.setText(f"보정 화면을 열지 못했습니다: {error}")
                    return
            self.page_attempts += 1
            if self.page_attempts >= 40:
                self.page_poll.stop()
                self.window.cursor_status.setText("보정 화면이 열리지 않았습니다. 다시 보정을 누르세요.")
            else:
                self.page_poll.start()
            return
        except (ValueError, RuntimeError, OSError) as error:
            self.page_poll.stop()
            self.window.cursor_status.setText(f"보정 화면 탐색 실패: {error}")
            return
        self.page_poll.stop()
        try:
            self.reader = BrowserReader(websocket, timeout_ms=1500)
            self.reader.connect()
            self.reference = self.reader.find_clickable("#reference")
            if self.reference is None:
                self.reader.close()
                self.reader = None
                self.page_attempts += 1
                if self.page_attempts >= 40:
                    raise RuntimeError("보정 기준 버튼을 찾지 못했습니다.")
                self.page_poll.start()
                return
            self.window.cursor_status.setText(
                "5초 안에 Chrome 페이지를 클릭해 활성화하고 '보정 기준' 버튼 중앙에 커서를 놓으세요.")
            self.window.calibrate_button.setEnabled(False)
            generation = self.calibration_generation
            QTimer.singleShot(5000, lambda: self.finish_calibration(generation))
        except (BrowserReaderError, RuntimeError, ValueError, OSError) as error:
            self.close()
            self.window.cursor_status.setText(f"보정 실패: {error}")

    def finish_calibration(self, generation):
        if generation != self.calibration_generation:
            return
        self.window.calibrate_button.setEnabled(True)
        if self.reader is None or self.reference is None:
            return
        try:
            if not self.reader.page_has_focus():
                raise RuntimeError("Chrome 페이지가 활성화되지 않았습니다.")
            fresh = self.reader.find_clickable("#reference")
            if fresh != self.reference:
                raise RuntimeError("기준 버튼 위치가 변경되었습니다.")
            self.control.calibrate(*fresh.center, scale=self.reader.device_pixel_ratio())
            self.calibration_geometry = self.reader.page_snapshot().geometry
            self.window.browser_status.setText(
                "보정 완료. 같은 Chrome 탭에서 LMS로 이동하세요. 로그인 후 버튼 위에 커서를 놓으면 이름을 표시합니다.")
            self.timer.start()
            self.update_cursor()
        except (BrowserReaderError, RuntimeError, ValueError, OSError) as error:
            self.close()
            self.window.cursor_status.setText(f"보정 실패: {error}")

    def update_cursor(self):
        if self.busy or self.reader is None:
            return
        self.busy = True
        try:
            if not self.reader.page_has_focus():
                self.window.cursor_status.setText("커서 아래 버튼: Chrome 페이지를 활성화하세요.")
                return
            if self.reader.device_pixel_ratio() != self.control.scale:
                raise RuntimeError("화면 배율이 바뀌었습니다. 다시 보정하세요.")
            x, y = pyautogui.position()
            target = self.reader.find_clickable_at(*self.control.screen_to_web(x, y))
            self.query_failures = 0
            if target is None:
                self.window.cursor_status.setText("커서 아래 버튼: 없음")
            else:
                name = target.label or "이름을 추정할 텍스트 없음"
                self.window.cursor_status.setText(f"커서 아래 버튼: {name} ({target.tag})")
        except BrowserReaderError as error:
            self.query_failures += 1
            if self.query_failures < 20:
                self.window.cursor_status.setText("커서 아래 버튼: 페이지를 읽는 중입니다…")
            else:
                self.close()
                self.window.cursor_status.setText(f"커서 감지 중지: {error}")
        except (RuntimeError, ValueError, OSError) as error:
            self.close()
            self.window.cursor_status.setText(f"커서 감지 중지: {error}")
        finally:
            self.busy = False

    def close(self):
        self.calibration_generation += 1
        self.page_poll.stop()
        self.timer.stop()
        self.query_failures = 0
        self.control.clear_calibration()
        self.calibration_geometry = None
        self.reference = None
        if self.reader is not None:
            self.reader.close()
            self.reader = None
        self.window.calibrate_button.setEnabled(True)


@dataclass(frozen=True)
class RegisteredLecture:
    identity: object
    list_url: str
    list_geometry: tuple
    player_url: str
    player_geometry: tuple
    player_control: ComputerControl
    video_identity: tuple = field(repr=False)
    duration: float
    navigation: str

    @property
    def wait_seconds(self):
        return self.duration + 180.0


class WorkflowCancelled(Exception):
    """Stop was requested while a CDP query ran its nested Qt event loop."""


def browser_pages(endpoint):
    """Read dedicated Chrome page metadata; never retain it on disk."""
    address = urlsplit(endpoint or "")
    if (address.scheme != "http" or address.hostname != "127.0.0.1"
            or not address.port or address.username or address.password
            or address.path not in ("", "/") or address.query or address.fragment):
        raise ValueError("연결한 전용 Chrome의 주소를 확인할 수 없습니다.")
    with build_opener(ProxyHandler({})).open(
            f"{endpoint}/json/list", timeout=1) as response:
        if urlsplit(response.geturl()).netloc != address.netloc:
            raise ValueError("브라우저 연결 주소가 변경되었습니다.")
        pages = json.load(response)
    if not isinstance(pages, list):
        raise ValueError("브라우저 탭 목록을 읽을 수 없습니다.")
    result = {}
    for page in pages:
        if not isinstance(page, dict) or page.get("type") != "page":
            continue
        websocket = page.get("webSocketDebuggerUrl", "")
        url = urlsplit(websocket)
        if (url.scheme != "ws" or url.hostname != "127.0.0.1"
                or url.port != address.port or url.username or url.password
                or not url.path.startswith("/devtools/page/")
                or url.query or url.fragment or not isinstance(page.get("id"), str)):
            raise ValueError("브라우저 탭 연결 정보를 확인할 수 없습니다.")
        result[page["id"]] = websocket
    return result


class LectureAutomation(QObject):
    """Observe registration, then process the memory queue using real input."""

    def __init__(self, window, connection, inspector):
        super().__init__(window)
        self.window = window
        self.connection = connection
        self.inspector = inspector
        self.queue = deque()
        self.state = "idle"
        self.phase = ""
        self.generation = 0
        self.busy = False
        self.list_reader = None
        self.stopping = False
        self.player_reader = None
        self.owns_player = False
        self.pending = None
        self.list_url = None
        self.list_geometry = None
        self.baseline_pages = set()
        self.deadline = 0.0
        self.wait_until = 0.0
        self.scrolled = False
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.tick)
        window.register_button.clicked.connect(self.begin_registration)
        window.finish_registration_button.clicked.connect(self.finish_registration)
        window.start_button.clicked.connect(self.start_playback)
        window.stop_button.clicked.connect(self.stop)
        window.clear_queue_button.clicked.connect(self.clear_queue)
        connection.socket.disconnected.connect(self.stop)

    def _guard(self, generation):
        if generation != self.generation or self.state in {"idle", "stopped"}:
            raise WorkflowCancelled()

    def _read(self, generation, operation, *args):
        try:
            result = operation(*args)
        except BrowserReaderError as error:
            self._guard(generation)
            name = getattr(operation, '__name__', 'CDP 조회')
            raise BrowserReaderError(f'{name}: {error}') from error
        self._guard(generation)
        return result

    def _set_state(self, state):
        self.state = state
        self.window.set_workflow_state(state, bool(self.queue))

    def _show_queue(self):
        self.window.set_queue([
            f"{index}. {item.identity.label or item.identity.tag} · "
            f"영상 {math.ceil(item.duration)}초 · 대기 {math.ceil(item.wait_seconds)}초 (+3분)"
            for index, item in enumerate(self.queue, 1)
        ])

    def _prepare(self):
        if self.busy:
            raise RuntimeError("현재 페이지 확인이 끝난 뒤 다시 실행하세요.")
        if self.inspector.busy:
            raise RuntimeError("커서 탐색이 끝난 뒤 다시 실행하세요.")
        if self.inspector.reader is None or self.inspector.calibration_geometry is None:
            raise RuntimeError("먼저 브라우저를 연결하고 좌표를 보정하세요.")
        self.inspector.control.offset
        self.list_reader = self.inspector.reader
        self.inspector.timer.stop()
        self.generation += 1

    @staticmethod
    def _verify_snapshot(snapshot, url, geometry):
        if snapshot.url != url:
            raise RuntimeError("등록한 페이지와 다릅니다. 해당 강의 목록 또는 재생창으로 돌아가세요.")
        if snapshot.geometry != geometry:
            raise RuntimeError("브라우저 위치·크기 또는 배율이 바뀌었습니다. 다시 보정하고 영상을 등록하세요.")

    def begin_registration(self):
        if self.state not in {"idle", "stopped"}:
            return
        try:
            self._prepare()
            self._set_state("registering")
            generation = self.generation
            snapshot = self._read(generation, self.list_reader.page_snapshot)
            if snapshot.geometry != self.inspector.calibration_geometry:
                raise RuntimeError("보정 이후 브라우저 위치·크기 또는 배율이 바뀌었습니다. 다시 보정하세요.")
            self.list_url, self.list_geometry = snapshot.url, snapshot.geometry
            if self.queue and (self.queue[0].list_url != self.list_url or
                               self.queue[0].list_geometry != self.list_geometry):
                raise RuntimeError("기존 큐와 같은 강의 목록·창 상태에서 추가 등록하세요. 새로 등록하려면 먼저 등록 목록을 비우세요.")
            self.baseline_pages = set(browser_pages(self.connection.endpoint))
            self._read(generation, self.list_reader.start_click_observation)
            self.window.registration_status.setText("강의 목록에서 등록할 영상 항목을 클릭하세요.")
            self.timer.start()
        except WorkflowCancelled:
            pass
        except (RuntimeError, ValueError, OSError) as error:
            self._fail(error)

    def finish_registration(self):
        if self.state not in {"registering", "waiting_list"} or not self.queue:
            return
        if self.busy:
            self.window.registration_status.setText("페이지 확인이 끝난 뒤 등록 완료를 다시 누르세요.")
            return
        try:
            generation = self.generation
            snapshot = self._read(generation, self.list_reader.page_snapshot)
            self._verify_snapshot(snapshot, self.list_url, self.list_geometry)
            # A final popup must be returned from manually before finishing.
            if self.player_reader is not None:
                if self.owns_player and set(browser_pages(self.connection.endpoint)) - self.baseline_pages:
                    raise RuntimeError("마지막 영상 재생창을 닫고 강의 목록으로 돌아온 뒤 등록 완료를 누르세요.")
                self._guard(generation)
                self._release_player()
                self._guard(generation)
            self._read(generation, self.list_reader.stop_click_observation)
            self.window.registration_status.setText(f"영상 {len(self.queue)}개 등록 완료")
            self._begin_playback()
        except WorkflowCancelled:
            pass
        except (RuntimeError, ValueError, OSError) as error:
            self.window.registration_status.setText(str(error))

    def start_playback(self):
        if self.state not in {"idle", "stopped"} or not self.queue:
            return
        try:
            self._prepare()
            self._begin_playback()
        except (RuntimeError, ValueError, OSError) as error:
            self._fail(error)

    def _begin_playback(self):
        self._set_state("playing")
        self.phase = "list"
        self.scrolled = False
        self.window.playback_status.setText("강의 목록이 있는 Chrome 페이지를 활성화하면 자동 재생을 시작합니다.")
        self.timer.start()

    def tick(self):
        if self.busy or self.state in {"idle", "stopped"}:
            return
        self.busy = True
        generation = self.generation
        try:
            if self.state == "registering":
                self._register_click(generation)
            elif self.state == "pending_player":
                self._register_player(generation)
            elif self.state == "waiting_list":
                self._registration_return(generation)
            elif self.state == "playing":
                self._play_tick(generation)
        except WorkflowCancelled:
            pass
        except BrowserReaderError as error:
            loading = self.state == "pending_player" or (self.state == "playing" and self.phase == "opening")
            if not (loading and error.during_navigation and time.monotonic() < self.deadline):
                self._fail(error)
        except (RuntimeError, ValueError, OSError, pyautogui.FailSafeException) as error:
            self._fail(error)
        finally:
            self.busy = False

    def _register_click(self, generation):
        clicked = self._read(generation, self.list_reader.take_clicked_identity)
        if clicked is not None:
            self._begin_player_registration(generation, clicked)
            return
        try:
            snapshot = self._read(generation, self.list_reader.page_snapshot)
        except BrowserReaderError as error:
            if error.during_navigation:
                clicked = self._read(generation, self.list_reader.take_clicked_identity)
                if clicked is not None:
                    self._begin_player_registration(generation, clicked)
                    return
            raise
        # The binding event can arrive while the snapshot query's event loop runs.
        clicked = self._read(generation, self.list_reader.take_clicked_identity)
        if clicked is not None:
            self._begin_player_registration(generation, clicked)
            return
        self._verify_snapshot(snapshot, self.list_url, self.list_geometry)
        if not self._read(generation, self.list_reader.observation_is_active):
            self._read(generation, self.list_reader.start_click_observation)

    def _begin_player_registration(self, generation, clicked):
        self.pending = clicked.identity
        self._set_state("pending_player")
        self.deadline = time.monotonic() + 30
        try:
            self._read(generation, self.list_reader.stop_click_observation)
        except BrowserReaderError as error:
            if not error.during_navigation:
                raise
        if any(item.identity == self.pending and item.list_url == self.list_url
               for item in self.queue):
            self.pending = None
            self._set_state("waiting_list")
            self.window.registration_status.setText("이미 등록한 강의입니다. 목록으로 돌아가 다음 강의를 선택하세요.")
            return
        self.window.registration_status.setText(
            "선택한 강의의 재생 페이지와 영상 정보가 준비되면 자동으로 등록합니다.")

    def _locate_player(self, generation, navigation=None):
        """Only the original calibrated tab is supported; new windows are deferred."""
        if self.player_reader is not None:
            return self.player_reader
        pages = browser_pages(self.connection.endpoint)
        self._guard(generation)
        if navigation == "new" or set(pages) - self.baseline_pages:
            raise RuntimeError("새 탭·창의 좌표 보정 처리는 보류 중입니다. 보정한 같은 탭에서 다시 등록하세요.")
        snapshot = self._read(generation, self.list_reader.page_snapshot)
        if snapshot.url != self.list_url:
            self._verify_calibration(self.list_reader, snapshot)
        if snapshot.has_focus and snapshot.url != self.list_url:
            video = self._read(generation, read_video_state, self.list_reader)
            if video is not None:
                self.player_reader = self.list_reader
                self.owns_player = False
                return self.player_reader
        return None

    def _verify_calibration(self, reader, snapshot):
        control = self.inspector.control
        control.offset
        if (reader is not self.list_reader or reader is not self.inspector.reader or
                snapshot.geometry != self.inspector.calibration_geometry or
                snapshot.geometry != self.list_geometry or
                snapshot.device_pixel_ratio != control.scale):
            raise RuntimeError("현재 탭에 좌표 보정을 적용할 수 없습니다. 다시 보정하고 같은 탭에서 등록하세요.")

    def _wait_for_registration(self):
        if time.monotonic() >= self.deadline:
            raise RuntimeError("재생 페이지·영상 메타데이터·가림 없는 플레이어를 확인하지 못했습니다. 목록에서 다시 등록하세요.")

    def _register_player(self, generation):
        if self.pending is None:
            raise RuntimeError("등록할 강의가 선택되지 않았습니다.")
        reader = self._locate_player(generation)
        if reader is None:
            self._wait_for_registration()
            return
        snapshot = self._read(generation, reader.page_snapshot)
        self._verify_calibration(reader, snapshot)
        if snapshot.url == self.list_url:
            raise RuntimeError("선택한 강의의 재생 페이지로 전환되지 않았습니다.")
        video = self._read(generation, read_video_state, reader)
        target = self._read(generation, reader.find_player_center)
        if not snapshot.has_focus or video is None or target is None:
            self._wait_for_registration()
            return
        if not math.isfinite(video.duration) or video.duration <= 0:
            raise RuntimeError("영상 길이를 확인할 수 없습니다.")
        fresh_target = self._read(generation, reader.find_player_center)
        fresh_video = self._read(generation, read_video_state, reader)
        fresh = self._read(generation, reader.page_snapshot)
        self._verify_snapshot(fresh, snapshot.url, snapshot.geometry)
        self._verify_calibration(reader, fresh)
        if (not fresh.has_focus or fresh_target != target or fresh_video is None or
                fresh_video.duration != video.duration):
            self._wait_for_registration()
            return
        item = RegisteredLecture(
            self.pending, self.list_url, self.list_geometry, snapshot.url,
            snapshot.geometry, self.inspector.control, target.identity,
            video.duration, "same")
        self.queue.append(item)
        self.pending = None
        self._show_queue()
        self._set_state("waiting_list")
        self.window.registration_status.setText(
            f"등록 완료: 영상 {math.ceil(item.duration)}초 + 안전 마진 180초. "
            "직접 재생창에서 강의 목록으로 돌아가 다음 영상을 선택하세요.")

    def _release_player(self):
        if self.player_reader is not None and self.owns_player:
            self.player_reader.close()
        self.player_reader = None
        self.owns_player = False

    def _registration_return(self, generation):
        snapshot = self._read(generation, self.list_reader.page_snapshot)
        if snapshot.url != self.list_url or not snapshot.has_focus:
            return
        self._verify_snapshot(snapshot, self.list_url, self.list_geometry)
        if self.owns_player:
            # If the popup is still open, retain it until the user closes it.
            pages = browser_pages(self.connection.endpoint)
            self._guard(generation)
            if set(pages) - self.baseline_pages:
                self.window.registration_status.setText("등록한 재생창을 닫고 강의 목록에서 다음 영상을 선택하세요.")
                return
        self._release_player()
        self._guard(generation)
        self.baseline_pages = set(browser_pages(self.connection.endpoint))
        self._guard(generation)
        self._read(generation, self.list_reader.start_click_observation)
        self._set_state("registering")
        self.window.registration_status.setText("다음 영상 항목을 클릭하거나 등록 완료를 누르세요.")

    def _play_tick(self, generation):
        if not self.queue:
            self.stop(completed=True)
            return
        item = self.queue[0]
        if self.phase in {"list", "locate"}:
            self._open_lecture(generation, item)
        elif self.phase == "opening":
            self._start_video(generation, item)
        elif self.phase == "starting":
            snapshot = self._read(generation, self.player_reader.page_snapshot)
            self._verify_snapshot(snapshot, item.player_url, item.player_geometry)
            self._verify_calibration(self.player_reader, snapshot)
            target = self._read(generation, self.player_reader.find_player_center)
            if target is None or target.identity != item.video_identity:
                raise RuntimeError("재생 확인 중 영상 대상이 바뀌거나 플레이어가 가려졌습니다.")
            video = self._read(generation, read_video_state, self.player_reader)
            if video is not None and not math.isclose(video.duration, item.duration, abs_tol=1, rel_tol=0):
                raise RuntimeError("재생 확인 중 영상 길이가 바뀌었습니다.")
            if snapshot.has_focus and video is not None and video.playing:
                self.wait_until = time.monotonic() + item.wait_seconds
                self.phase = "watching"
            elif time.monotonic() >= self.deadline:
                raise RuntimeError("플레이어 중앙을 클릭했지만 재생이 확인되지 않았습니다. LMS의 중앙 클릭 동작을 확인하세요.")
        elif self.phase == "watching":
            remaining = max(0, math.ceil(self.wait_until - time.monotonic()))
            self.window.playback_status.setText(
                f"재생 중: {item.identity.label or item.identity.tag} · 다음 전환까지 {remaining}초 · 남은 영상 {len(self.queue)}개")
            if remaining == 0:
                self.phase = "return"
        elif self.phase == "return":
            self._return_to_list(generation, item)
        elif self.phase == "returning":
            snapshot = self._read(generation, self.list_reader.page_snapshot)
            if snapshot.url == item.list_url and snapshot.has_focus:
                self._verify_snapshot(snapshot, item.list_url, item.list_geometry)
                self._release_player()
                self._guard(generation)
                self.queue.popleft()
                self._show_queue()
                self.phase = "list"
                self.scrolled = False
            elif time.monotonic() >= self.deadline:
                raise RuntimeError("강의 목록으로 돌아온 상태를 확인하지 못했습니다.")

    def _open_lecture(self, generation, item):
        snapshot = self._read(generation, self.list_reader.page_snapshot)
        self._verify_snapshot(snapshot, item.list_url, item.list_geometry)
        self._verify_calibration(self.list_reader, snapshot)
        if not snapshot.has_focus:
            self.window.playback_status.setText("등록한 강의 목록의 Chrome 페이지를 활성화하세요.")
            return
        target = self._read(generation, self.list_reader.find_registered_clickable, item.identity)
        if target is None:
            if not self.scrolled:
                found = self._read(generation, self.list_reader.scroll_registered_into_view, item.identity)
                if not found:
                    raise RuntimeError("등록한 영상 항목을 찾지 못했습니다.")
                self.scrolled = True
                self.phase = "locate"
                self.deadline = time.monotonic() + 5
            elif time.monotonic() >= self.deadline:
                raise RuntimeError("영상 항목이 숨겨져 있거나 가려져 있어 클릭할 수 없습니다.")
            return
        self.baseline_pages = set(browser_pages(self.connection.endpoint))
        self._guard(generation)
        fresh = self._read(generation, self.list_reader.find_registered_clickable, item.identity)
        current = self._read(generation, self.list_reader.page_snapshot)
        self._verify_snapshot(current, item.list_url, item.list_geometry)
        self._verify_calibration(self.list_reader, current)
        if not current.has_focus or fresh is None or fresh != target:
            raise RuntimeError("클릭 직전에 영상 항목의 위치 또는 활성 페이지가 바뀌었습니다.")
        self.list_url, self.list_geometry = item.list_url, item.list_geometry
        self.phase = "opening"
        self.deadline = time.monotonic() + 30
        self.inspector.control.click(*fresh.center)
        self.window.playback_status.setText(f"영상 열기: {item.identity.label or item.identity.tag}")

    def _start_video(self, generation, item):
        reader = self._locate_player(generation, item.navigation)
        if reader is None:
            if time.monotonic() >= self.deadline:
                raise RuntimeError("등록한 영상 재생창이 열리지 않았습니다.")
            return
        snapshot = self._read(generation, reader.page_snapshot)
        self._verify_snapshot(snapshot, item.player_url, item.player_geometry)
        self._verify_calibration(reader, snapshot)
        if not snapshot.has_focus:
            self.window.playback_status.setText("영상 재생창의 Chrome 페이지를 활성화하세요.")
            return
        video = self._read(generation, read_video_state, reader)
        if video is None:
            if time.monotonic() >= self.deadline:
                raise RuntimeError("영상 메타데이터가 준비되지 않았습니다.")
            return
        if not math.isclose(video.duration, item.duration, abs_tol=1, rel_tol=0):
            raise RuntimeError("등록한 영상 길이와 현재 영상이 다릅니다.")
        target = self._read(generation, reader.find_player_center)
        if target is None or target.identity != item.video_identity:
            raise RuntimeError("등록한 영상과 현재 대상이 다르거나 플레이어 중앙이 가려졌습니다.")
        fresh_target = self._read(generation, reader.find_player_center)
        fresh_video = self._read(generation, read_video_state, reader)
        fresh = self._read(generation, reader.page_snapshot)
        self._verify_snapshot(fresh, item.player_url, item.player_geometry)
        self._verify_calibration(reader, fresh)
        if (not fresh.has_focus or fresh_target != target or fresh_video is None or
                not math.isclose(fresh_video.duration, item.duration, abs_tol=1, rel_tol=0)):
            raise RuntimeError("재생 직전에 활성 페이지·영상·플레이어 위치가 바뀌었습니다.")
        if fresh_video.playing:
            self.wait_until = time.monotonic() + item.wait_seconds
            self.phase = "watching"
        elif fresh_video.paused and not fresh_video.ended:
            self.phase = "starting"
            self.deadline = time.monotonic() + 10
            self.inspector.control.click(*fresh_target.position.center)
        else:
            raise RuntimeError("현재 영상의 재생 상태가 불확실하거나 이미 종료되었습니다.")

    def _return_to_list(self, generation, item):
        if item.navigation != "same":
            raise RuntimeError("새 탭·창 처리는 보류 중입니다. 같은 탭에서 다시 등록하세요.")
        snapshot = self._read(generation, self.player_reader.page_snapshot)
        self._verify_snapshot(snapshot, item.player_url, item.player_geometry)
        self._verify_calibration(self.player_reader, snapshot)
        if not snapshot.has_focus:
            self.window.playback_status.setText("대기 완료. 영상 재생창을 활성화하면 강의 목록으로 돌아갑니다.")
            return
        self.phase = "returning"
        self.deadline = time.monotonic() + 30
        self.inspector.control.go_back()

    def _fail(self, error):
        self.stop()
        self.window.playback_status.setText(f"중지: {error}")

    def clear_queue(self):
        if self.busy or self.state not in {"idle", "stopped"}:
            return
        self.queue.clear()
        self._show_queue()
        self._set_state("idle")
        self.window.registration_status.setText("등록 목록을 비웠습니다. 강의 목록에서 영상을 새로 등록하세요.")
        self.window.playback_status.setText("자동 재생 대기")

    def stop(self, completed=False):
        if self.stopping:
            return
        self.stopping = True
        was_busy = self.busy
        self.busy = True
        self.generation += 1
        self.timer.stop()
        self._set_state("idle" if completed else "stopped")
        # Detach observers without sending input or altering the current page.
        for reader in {self.list_reader, self.player_reader} - {None}:
            try:
                reader.stop_click_observation()
            except (RuntimeError, ValueError, OSError):
                pass
        self._release_player()
        self.pending = None
        self.phase = ""
        self.window.registration_status.setText("영상 등록 대기" if completed else "등록/자동 재생 중지 · 완료 전 영상은 큐에 유지됩니다. 강의 목록으로 돌아가 재시작하거나 등록 목록을 비우세요.")
        self.window.playback_status.setText("모든 영상의 순차 재생을 완료했습니다." if completed else "자동 재생 중지")
        if self.inspector.reader is not None and self.inspector.calibration_geometry is not None:
            self.inspector.timer.start()
        self.busy = was_busy
        self.stopping = False


def launch_coordinate_test(window, connection):
    """Run the existing interactive test in its own Windows console."""
    if sys.platform != "win32":
        window.browser_status.setText("좌표 변환 테스트 콘솔은 Windows 환경에서 지원합니다.")
        return
    if (not connection.endpoint or connection.socket.state()
            != QAbstractSocket.SocketState.ConnectedState):
        window.browser_status.setText("먼저 브라우저를 연결하세요.")
        return
    directory = Path(__file__).resolve().parent
    script = directory / "test_mouse_control.py"
    if not script.is_file():
        window.browser_status.setText("test_mouse_control.py 파일을 찾을 수 없습니다.")
        return
    # pythonw.exe has no console input/output; use the same environment's python.exe.
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    try:
        subprocess.Popen(
            [str(executable), "-i", str(script), "--cdp-endpoint", connection.endpoint,
             "--chrome-executable", str(chrome_executable()),
             "--chrome-profile", str(connection.profile)],
            cwd=str(directory),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    except OSError as error:
        window.browser_status.setText(f"테스트 콘솔을 실행하지 못했습니다: {error}")
        return
    window.browser_status.setText("테스트 콘솔에서 test.html을 준비합니다. 콘솔 안내에 따라 진행하세요.")


def chrome_executable():
    """Find installed Windows Chrome without asking for internal settings."""
    import winreg

    key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as handle:
                    path = Path(winreg.QueryValue(handle, None).strip('"'))
                    if path.is_file():
                        return path
            except OSError:
                pass
    for variable in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        root = os.environ.get(variable)
        if root:
            path = Path(root) / "Google/Chrome/Application/chrome.exe"
            if path.is_file():
                return path
    raise OSError("Chrome을 찾지 못했습니다. Google Chrome 설치 상태를 확인하세요.")


class BrowserConnection(QObject):
    """Launch persistent dedicated Chrome and keep a browser-level CDP socket.

    No tab is selected here and no CDP commands change browser/page state.
    Chrome owns session persistence; the application never exports credentials.
    """

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.profile = None
        self.process = None
        self.endpoint = None
        self._existing = False
        self._working = False
        self.socket = QWebSocket()
        self.socket.connected.connect(self._connected)
        self.socket.disconnected.connect(self._disconnected)
        self.socket.errorOccurred.connect(self._socket_error)
        self.poll = QTimer(self)
        self.poll.setInterval(250)
        self.poll.timeout.connect(self._try_endpoint)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self._timed_out)
        window.browser_button.clicked.connect(self.connect_browser)

    def connect_browser(self):
        if self._working:
            return
        if self.socket.state() == QAbstractSocket.SocketState.ConnectedState:
            self.window.browser_status.setText("전용 Chrome이 연결되어 있습니다.")
            return
        try:
            if sys.platform != "win32":
                raise OSError("브라우저 연결은 Windows 환경에서 지원합니다.")
            root = os.environ.get("LOCALAPPDATA")
            if not root or not Path(root).is_absolute():
                raise OSError("Windows 애플리케이션 데이터 경로를 확인할 수 없습니다.")
            self.profile = Path(root) / "CatchCatch" / "ChromeProfile"
            self.profile.mkdir(parents=True, exist_ok=True)
            self._working = True
            self.window.browser_button.setEnabled(False)
            self.window.browser_status.setText("전용 Chrome에 연결하는 중입니다…")
            self.deadline.start(15000)
            self._existing = True
            if not self._try_endpoint():
                self._launch()
        except OSError as error:
            self._fail(str(error))

    def _launch(self):
        self._existing = False
        page = Path(__file__).resolve().with_name("test.html")
        if not page.is_file():
            raise OSError("보정 화면 test.html을 찾을 수 없습니다.")
        # Remove only stale discovery metadata, never the persistent profile.
        (self.profile / "DevToolsActivePort").unlink(missing_ok=True)
        self.process = subprocess.Popen([
            str(chrome_executable()),
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            f"--user-data-dir={self.profile}",
            "--no-first-run", "--no-default-browser-check", "--new-window",
            page.as_uri(),
        ])
        self.poll.start()

    def _try_endpoint(self):
        try:
            lines = (self.profile / "DevToolsActivePort").read_text(
                encoding="utf-8").splitlines()
            port = int(lines[0])
            path = lines[1]
            if not 1 <= port <= 65535 or not path.startswith("/devtools/browser/"):
                return False
            url = QUrl(f"ws://127.0.0.1:{port}{path}")
            if (not url.isValid() or url.host() != "127.0.0.1"
                    or url.port() != port or url.hasQuery() or url.hasFragment()):
                return False
        except (OSError, ValueError, IndexError):
            return False
        self.poll.stop()
        self.endpoint = f"http://127.0.0.1:{port}"
        self.socket.open(url)
        return True

    def _connected(self):
        self._working = False
        self.poll.stop()
        self.deadline.stop()
        self.window.browser_button.setEnabled(True)
        self.window.browser_status.setText(
            "브라우저 연결 완료. 열린 전용 Chrome에서 LMS를 이용하세요. "
            "최초 이용 시 로그인하세요.")

    def _socket_error(self, _error):
        if not self._working:
            return
        self.endpoint = None
        if self._existing:
            try:
                self._launch()
            except OSError as error:
                self._fail(str(error))
        else:
            self.poll.start()

    def _disconnected(self):
        if not self._working:
            self.endpoint = None
            self.window.browser_status.setText("브라우저 연결이 끊겼습니다. 다시 연결하세요.")

    def _timed_out(self):
        self._fail("Chrome에 연결하지 못했습니다. 전용 Chrome을 닫은 뒤 다시 연결하세요.")

    def _fail(self, message):
        self.poll.stop()
        self.deadline.stop()
        self._working = False
        self.socket.abort()
        self.endpoint = None
        self.window.browser_button.setEnabled(True)
        self.window.browser_status.setText(message)

    def close(self):
        self.poll.stop()
        self.deadline.stop()
        self._working = False
        self.socket.abort()
        # Leave Chrome open so the user can continue browsing and save sessions.


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    connection = BrowserConnection(window)
    inspector = CursorInspector(window, connection)
    automation = LectureAutomation(window, connection, inspector)
    window.coordinate_test_button.clicked.connect(lambda: launch_coordinate_test(window, connection))
    app.aboutToQuit.connect(automation.stop)
    app.aboutToQuit.connect(inspector.close)
    app.aboutToQuit.connect(connection.close)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
