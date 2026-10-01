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
from typing import Optional

import pyautogui

from browser_reader import BrowserReader, ElementPosition


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

    def calibrate(self, web_x: float, web_y: float, *, scale: float = 1.0):
        """Calibrate at a known web point using the browser's pixel ratio."""
        if not math.isfinite(web_x) or not math.isfinite(web_y):
            raise ValueError('Coordinates must be finite')
        if isinstance(scale, bool) or not math.isfinite(scale) or scale <= 0:
            raise ValueError('Scale must be finite and positive')
        screen_x, screen_y = pyautogui.position()
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
