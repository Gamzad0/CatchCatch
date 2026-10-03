"""PyAutoGUI input and conversion between viewport and screen coordinates.

Calibration requires the cursor to be at the reference element's CENTER.
Calibration applies a uniform physical-pixel/CSS-pixel scale and an offset.
Recalibrate after window movement, browser zoom, DPI/display changes,
or changes to browser chrome. Keep the intended tab foreground and unobscured.
Offsets stay in memory; no authentication or browser data is persisted.

Example (caller provides the chosen tab URL and reference selector)::

    app = QCoreApplication([])  # from PySide6.QtCore; keep alive
    reader = BrowserReader(websocket_url)
    reader.connect()
    control = ComputerControl()
    reference = reader.find_clickable(reference_selector)
    if reference is None:
        raise RuntimeError('Reference element is unavailable')
    input('Place cursor at reference center, then press Enter: ')
    control.calibrate(*reference.center, scale=reader.device_pixel_ratio())
    input('Place cursor over the target, then press Enter: ')
    target = control.find_clickable_under_cursor(reader)
    if target is not None:
        control.move_to(*target.center)
    reader.close()

Use control.click(*fresh_target.center) only after confirming the active tab
and querying the target again. A returned position is a snapshot, not a handle.
"""

import math
import sys
from typing import Optional

import pyautogui

from browser_reader import BrowserReader, ElementPosition


class WindowLayout:
    """Place identified native windows on the primary display before calibration.

    Use the primary display because PyAutoGUI screen checks support that display.
    Call ready() until the requested bounds are applied and stable; callers own
    the timeout and must not read calibration coordinates before it succeeds.
    """

    def __init__(self, right_window, browser_title=None):
        if sys.platform != 'win32':
            raise RuntimeError('창 자동 배치는 Windows 환경에서 지원합니다.')
        try:
            import win32api
            import win32con
            import win32gui
            import pywintypes
        except ImportError as error:
            raise RuntimeError('창 자동 배치에 필요한 pywin32를 찾을 수 없습니다.') from error
        self.gui, self.constants, self.native_error = win32gui, win32con, pywintypes.error
        self.previous = None
        try:
            if not right_window or not win32gui.IsWindowVisible(right_window):
                raise RuntimeError('화면에 표시된 CatchCatch 앱 창을 찾지 못했습니다.')
            monitor = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
            left, top, right, bottom = win32api.GetMonitorInfo(monitor)['Work']
            middle = left + (right - left) // 2
            self.targets = {right_window: (middle, top, right, bottom)}
            if browser_title is not None:
                if not browser_title:
                    raise RuntimeError('보정 페이지의 창 제목을 확인하지 못했습니다.')
                matches = []

                def collect(handle, unused):
                    if (win32gui.IsWindowVisible(handle)
                            and win32gui.GetClassName(handle) == 'Chrome_WidgetWin_1'
                            and win32gui.GetWindowText(handle).startswith(browser_title + ' - ')):
                        matches.append(handle)

                win32gui.EnumWindows(collect, None)
                if len(matches) != 1:
                    raise RuntimeError(
                        '보정 페이지가 표시된 Chrome 창을 하나로 확인하지 못했습니다. '
                        '전용 Chrome에서 보정 탭을 선택하고 중복 보정 창을 닫은 뒤 다시 시도하세요.')
                if matches[0] == right_window:
                    raise RuntimeError('앱 창과 Chrome 창이 동일합니다.')
                self.targets[matches[0]] = (left, top, middle, bottom)
        except self.native_error as error:
            raise RuntimeError(f'창 탐색 실패: {error}') from error

    def start(self):
        """Restore maximized/minimized windows, then request native bounds."""
        try:
            for handle, (left, top, right, bottom) in self.targets.items():
                self.gui.ShowWindow(handle, self.constants.SW_RESTORE)
                self.gui.SetWindowPos(
                    handle, self.constants.HWND_TOP, left, top, right - left, bottom - top,
                    self.constants.SWP_NOACTIVATE | self.constants.SWP_SHOWWINDOW)
        except self.native_error as error:
            raise RuntimeError(f'창 배치 실패: {error}') from error

    def ready(self):
        """Require two consecutive matching native window-bound observations."""
        try:
            actual = {}
            for handle in self.targets:
                if (not self.gui.IsWindow(handle) or not self.gui.IsWindowVisible(handle)
                        or self.gui.IsIconic(handle)
                        or self.gui.GetWindowPlacement(handle)[1]
                        == self.constants.SW_SHOWMAXIMIZED):
                    self.previous = None
                    return False
                actual[handle] = self.gui.GetWindowRect(handle)
            stable = actual == self.targets and actual == self.previous
            self.previous = actual
            return stable
        except self.native_error as error:
            raise RuntimeError(f'창 배치 확인 실패: {error}') from error


class ComputerControl:
    def __init__(self):
        self._offset = None
        self._scale = 1.0

    def clear_calibration(self):
        self._offset = None
        self._scale = 1.0

    @property
    def offset(self):
        if self._offset is None:
            raise RuntimeError('Calibrate coordinates before using mouse input')
        return self._offset

    @property
    def scale(self):
        self.offset  # Require a valid calibration.
        return self._scale

    def calibrate(self, web_x: float, web_y: float, *, scale: float = 1.0,
                  screen_point=None):
        """Calibrate at a known web point using the browser's pixel ratio."""
        if not math.isfinite(web_x) or not math.isfinite(web_y):
            raise ValueError('Coordinates must be finite')
        if isinstance(scale, bool) or not math.isfinite(scale) or scale <= 0:
            raise ValueError('Scale must be finite and positive')
        screen_x, screen_y = pyautogui.position() if screen_point is None else screen_point
        if not math.isfinite(screen_x) or not math.isfinite(screen_y):
            raise ValueError('Screen coordinates must be finite')
        self._offset = screen_x - web_x * scale, screen_y - web_y * scale
        self._scale = scale
        return self._offset

    def web_to_screen(self, web_x: float, web_y: float):
        offset_x, offset_y = self.offset
        if not math.isfinite(web_x) or not math.isfinite(web_y):
            raise ValueError('Coordinates must be finite')
        return web_x * self._scale + offset_x, web_y * self._scale + offset_y

    def screen_to_web(self, screen_x: float, screen_y: float):
        offset_x, offset_y = self.offset
        if not math.isfinite(screen_x) or not math.isfinite(screen_y):
            raise ValueError('Coordinates must be finite')
        return (screen_x - offset_x) / self._scale, (screen_y - offset_y) / self._scale

    def find_clickable_under_cursor(self, reader: BrowserReader) -> Optional[ElementPosition]:
        """Read cursor, reverse the offset, then ask CDP to hit-test the DOM."""
        screen_x, screen_y = pyautogui.position()
        return reader.find_clickable_at(*self.screen_to_web(screen_x, screen_y))

    def _screen_point(self, web_x, web_y):
        x, y = self.web_to_screen(web_x, web_y)
        point = round(x), round(y)
        if not pyautogui.onScreen(*point):
            raise ValueError('Converted point is outside the supported screen')
        return point

    def move_to(self, web_x: float, web_y: float):
        pyautogui.moveTo(*self._screen_point(web_x, web_y))

    def click(self, web_x: float, web_y: float):
        """Caller must query fresh geometry and verify the foreground tab first."""
        pyautogui.click(*self._screen_point(web_x, web_y))

    def go_back(self):
        """Return from a verified same-tab player using real keyboard input."""
        if sys.platform != 'win32':
            pyautogui.hotkey('alt', 'left')
            return
        pyautogui.failSafeCheck()
        try:
            import win32api
            import win32con
            import pywintypes
        except ImportError as error:
            raise RuntimeError('뒤로 가기 입력에 필요한 pywin32를 찾을 수 없습니다.') from error
        try:
            alt_scan = win32api.MapVirtualKey(win32con.VK_MENU, 0)
            left_scan = win32api.MapVirtualKey(win32con.VK_LEFT, 0)
            # Distinguish the navigation arrow from its numeric-keypad counterpart.
            extended = win32con.KEYEVENTF_EXTENDEDKEY
            released = win32con.KEYEVENTF_KEYUP
            try:
                win32api.keybd_event(win32con.VK_MENU, alt_scan, 0, 0)
                try:
                    win32api.keybd_event(win32con.VK_LEFT, left_scan, extended, 0)
                finally:
                    win32api.keybd_event(win32con.VK_LEFT, left_scan, extended | released, 0)
            finally:
                win32api.keybd_event(win32con.VK_MENU, alt_scan, released, 0)
        except pywintypes.error as error:
            raise RuntimeError(f'뒤로 가기 키 입력 실패: {error}') from error

    def close_player(self):
        """Close only a verified foreground player tab/window."""
        pyautogui.hotkey('ctrl', 'w')
