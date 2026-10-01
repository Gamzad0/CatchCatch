"""Read-only CDP queries in top-level viewport CSS pixels.

Create a QCoreApplication (or QApplication) before BrowserReader. Pass the
chosen tab's webSocketDebuggerUrl from the browser's /json/list endpoint.
Use this object only from the Qt thread that created it. No UI is required.
Frames and shadow roots are intentionally not traversed.
"""

import json
import math
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket


class BrowserReaderError(RuntimeError):
    """Connection, JavaScript, or ambiguous element lookup failure."""


@dataclass(frozen=True)
class ElementPosition:
    tag: str
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self):
        return self.x + self.width / 2, self.y + self.height / 2


# DOM observation only: no clicks, scrolling, focus, or page state writes.
_ELEMENT_POSITION = """
    if (!el) return null;
    if (el.matches('iframe, frame'))
        throw new Error('Frame contents are not supported');
    const clickable = el.closest('button, a, input, [role="button"]');
    if (!clickable) return null;
    const style = getComputedStyle(clickable);
    if (clickable.matches(':disabled') ||
        clickable.closest('[inert], [aria-disabled="true"]') ||
        style.visibility !== 'visible' || style.pointerEvents === 'none' ||
        Number(style.opacity) === 0) return null;
    const rect = clickable.getBoundingClientRect();
    const cx = rect.x + rect.width / 2;
    const cy = rect.y + rect.height / 2;
    if (rect.width <= 0 || rect.height <= 0 ||
        cx < 0 || cy < 0 || cx >= innerWidth || cy >= innerHeight) return null;
    const hit = document.elementFromPoint(cx, cy);
    if (!hit || (hit !== clickable && !clickable.contains(hit))) return null;
    if (hit.closest('button, a, input, [role="button"]') !== clickable) return null;
    return {tag: clickable.tagName.toLowerCase(), x: rect.x, y: rect.y,
            width: rect.width, height: rect.height};
"""


class BrowserReader:
    """Synchronous, bounded CDP queries for one explicitly selected tab."""

    def __init__(self, websocket_url: str, timeout_ms: int = 5000):
        if QCoreApplication.instance() is None:
            raise BrowserReaderError('Create QCoreApplication before BrowserReader')
        url = QUrl(websocket_url)
        if not url.isValid() or url.scheme() not in ('ws', 'wss') or not url.host():
            raise ValueError('A tab webSocketDebuggerUrl is required')
        if timeout_ms <= 0:
            raise ValueError('timeout_ms must be positive')
        self._url = url
        self._timeout_ms = timeout_ms
        self._socket = QWebSocket()
        self._request_id = 0
        self._busy = False

    def _exchange(self, action, expected_signal, accept):
        if self._busy:
            raise BrowserReaderError('Concurrent or nested CDP calls are not supported')
        self._busy = True
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        outcome = {}

        def received(*args):
            value = accept(*args)
            if value is not None:
                outcome['value'] = value
                loop.quit()

        def failed(*args):
            outcome['error'] = self._socket.errorString() or 'CDP disconnected'
            loop.quit()

        expected_signal.connect(received)
        self._socket.errorOccurred.connect(failed)
        self._socket.disconnected.connect(failed)
        timer.timeout.connect(loop.quit)
        try:
            timer.start(self._timeout_ms)
            action()
            if not outcome:
                loop.exec()
            if 'error' in outcome:
                raise BrowserReaderError(outcome['error'])
            if 'value' not in outcome:
                self._socket.abort()
                raise BrowserReaderError('CDP operation timed out')
            return outcome['value']
        finally:
            timer.stop()
            expected_signal.disconnect(received)
            self._socket.errorOccurred.disconnect(failed)
            self._socket.disconnected.disconnect(failed)
            self._busy = False

    def connect(self):
        if self._socket.state() == QAbstractSocket.SocketState.ConnectedState:
            return
        self._exchange(lambda: self._socket.open(self._url),
                       self._socket.connected, lambda: True)

    def close(self):
        self._socket.abort()

    def _evaluate(self, expression):
        if self._socket.state() != QAbstractSocket.SocketState.ConnectedState:
            raise BrowserReaderError('CDP is not connected')
        self._request_id += 1
        request_id = self._request_id

        def accept(message):
            try:
                data = json.loads(message)
            except (ValueError, TypeError):
                return None
            return data if isinstance(data, dict) and data.get('id') == request_id else None

        payload = json.dumps({'id': request_id, 'method': 'Runtime.evaluate',
                              'params': {'expression': expression,
                                         'returnByValue': True}})
        response = self._exchange(lambda: self._socket.sendTextMessage(payload),
                                  self._socket.textMessageReceived, accept)
        if 'error' in response:
            raise BrowserReaderError('CDP rejected the DOM query')
        result = response.get('result', {})
        if 'exceptionDetails' in result:
            raise BrowserReaderError('DOM query failed; check selector and page context')
        remote = result.get('result', {})
        if 'value' not in remote:
            raise BrowserReaderError('CDP returned no query value')
        return remote['value']

    def _position(self, lookup) -> Optional[ElementPosition]:
        value = self._evaluate('(() => {' + lookup + _ELEMENT_POSITION + '})()')
        if value is None:
            return None
        return ElementPosition(**value)

    def find_clickable(self, selector: str) -> Optional[ElementPosition]:
        """Find a unique selector match, then its closest clickable ancestor.

        None means absent, disabled, hidden, or unsafe to click at its center.
        Multiple matches and invalid selectors raise BrowserReaderError.
        """
        if not selector.strip():
            raise ValueError('selector must not be empty')
        return self._position(
            'const matches = document.querySelectorAll(' + json.dumps(selector) + ');'
            'if (matches.length > 1) throw new Error("Ambiguous selector");'
            'const el = matches[0];')

    def find_clickable_at(self, web_x: float, web_y: float) -> Optional[ElementPosition]:
        """Hit-test viewport coordinates and return the clickable ancestor."""
        if not math.isfinite(web_x) or not math.isfinite(web_y):
            raise ValueError('Coordinates must be finite')
        return self._position(
            'const el = document.elementFromPoint(' +
            json.dumps(web_x) + ', ' + json.dumps(web_y) + ');')
