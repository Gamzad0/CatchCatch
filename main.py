import sys
import os
import subprocess
import json
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from urllib.parse import urlsplit

import pyautogui

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import QApplication

from ui import MainWindow
from browser_reader import BrowserReader, BrowserReaderError
from computer_control import ComputerControl


class CursorInspector(QObject):
    """Read the one selected Chrome page and show the hovered button."""

    def __init__(self, window, connection):
        super().__init__(window)
        self.window = window
        self.connection = connection
        self.reader = None
        self.control = ComputerControl()
        self.reference = None
        self.busy = False
        self.timer = QTimer(self)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.update_cursor)
        window.calibrate_button.clicked.connect(self.begin_calibration)
        connection.socket.disconnected.connect(self.close)

    def _single_page_url(self):
        endpoint = self.connection.endpoint
        if not endpoint or self.connection.socket.state() != QAbstractSocket.SocketState.ConnectedState:
            raise RuntimeError("먼저 브라우저를 연결하세요.")
        address = urlsplit(endpoint)
        if address.scheme != "http" or address.hostname != "127.0.0.1" or not address.port:
            raise RuntimeError("Chrome 연결 주소를 확인할 수 없습니다.")
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"{endpoint}/json/list", timeout=3) as response:
            tabs = json.load(response)
        pages = [tab for tab in tabs if isinstance(tab, dict) and tab.get("type") == "page"
                 and tab.get("url", "").startswith(("http:", "https:", "file:"))]
        if len(pages) != 1:
            raise RuntimeError("전용 Chrome에 웹페이지 탭을 하나만 열고 다시 보정하세요.")
        websocket = pages[0].get("webSocketDebuggerUrl", "")
        url = urlsplit(websocket)
        if (url.scheme != "ws" or url.hostname != "127.0.0.1" or
                url.port != address.port or not url.path.startswith("/devtools/page/")):
            raise RuntimeError("웹페이지 탭의 CDP 주소를 확인할 수 없습니다.")
        return websocket

    def begin_calibration(self):
        self.close()
        selector = self.window.reference_selector.text().strip()
        if not selector:
            self.window.cursor_status.setText("보정 기준 버튼의 CSS 선택자를 입력하세요.")
            return
        try:
            self.reader = BrowserReader(self._single_page_url(), timeout_ms=1500)
            self.reader.connect()
            self.reference = self.reader.find_clickable(selector)
            if self.reference is None:
                raise RuntimeError("보정 기준 버튼을 찾지 못했습니다.")
            self.window.cursor_status.setText("3초 안에 Chrome으로 이동해 기준 버튼 중앙에 커서를 놓으세요.")
            self.window.calibrate_button.setEnabled(False)
            QTimer.singleShot(3000, self.finish_calibration)
        except (BrowserReaderError, RuntimeError, ValueError, OSError) as error:
            self.close()
            self.window.cursor_status.setText(f"보정 실패: {error}")

    def finish_calibration(self):
        self.window.calibrate_button.setEnabled(True)
        if self.reader is None or self.reference is None:
            return
        try:
            if not self.reader.page_has_focus():
                raise RuntimeError("Chrome 페이지가 활성화되지 않았습니다.")
            fresh = self.reader.find_clickable(self.window.reference_selector.text().strip())
            if fresh != self.reference:
                raise RuntimeError("기준 버튼 위치가 변경되었습니다.")
            self.control.calibrate(*fresh.center, scale=self.reader.device_pixel_ratio())
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
            if target is None:
                self.window.cursor_status.setText("커서 아래 버튼: 없음")
            else:
                name = target.label or "이름을 추정할 텍스트 없음"
                self.window.cursor_status.setText(f"커서 아래 버튼: {name} ({target.tag})")
        except (BrowserReaderError, RuntimeError, ValueError, OSError) as error:
            self.close()
            self.window.cursor_status.setText(f"커서 감지 중지: {error}")
        finally:
            self.busy = False

    def close(self):
        self.timer.stop()
        self.control.clear_calibration()
        self.reference = None
        if self.reader is not None:
            self.reader.close()
            self.reader = None
        self.window.calibrate_button.setEnabled(True)


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
        # Remove only stale discovery metadata, never the persistent profile.
        (self.profile / "DevToolsActivePort").unlink(missing_ok=True)
        self.process = subprocess.Popen([
            str(chrome_executable()),
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            f"--user-data-dir={self.profile}",
            "--no-first-run", "--no-default-browser-check", "--new-window",
            "about:blank",
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
    window.coordinate_test_button.clicked.connect(lambda: launch_coordinate_test(window, connection))
    app.aboutToQuit.connect(connection.close)
    app.aboutToQuit.connect(inspector.close)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
