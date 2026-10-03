"""CDP observation in top-level viewport CSS pixels.

Create a QCoreApplication (or QApplication) before BrowserReader. Pass the
chosen tab's webSocketDebuggerUrl from the browser's /json/list endpoint.
Use this object only from the Qt thread that created it. No UI is required.
Frames and shadow roots are intentionally not traversed.
The one page operation is the explicitly requested registered-item scroll.
"""

import json
import math
from collections import deque
from dataclasses import asdict, dataclass
from typing import Optional
from uuid import uuid4

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
    label: str = ''

    @property
    def center(self):
        return self.x + self.width / 2, self.y + self.height / 2


@dataclass(frozen=True)
class ElementIdentity:
    """Reusable selector and identity checks; kept only in the session queue."""

    selector: str
    tag: str
    label: str
    attributes: tuple[tuple[str, str], ...]
    context: str


@dataclass(frozen=True)
class ClickedElement:
    identity: ElementIdentity
    position: ElementPosition


@dataclass(frozen=True)
class VideoCapture:
    web_x: float
    web_y: float
    duration: float


@dataclass(frozen=True)
class PageSnapshot:
    """Transient page URL and the geometry needed to validate calibration."""

    url: str
    has_focus: bool
    screen_x: float
    screen_y: float
    outer_width: float
    outer_height: float
    viewport_width: float
    viewport_height: float
    device_pixel_ratio: float

    @property
    def geometry(self):
        return (self.screen_x, self.screen_y, self.outer_width, self.outer_height,
                self.viewport_width, self.viewport_height, self.device_pixel_ratio)


# Shared by the existing button lookup and registered-element observation.
_ELEMENT_HELPERS = r"""
const clickableSelector = 'button, a, input, [role="button"]';
function labelOf(clickable) {
    const labelledBy = (clickable.getAttribute('aria-labelledby') || '')
        .split(/\s+/).filter(Boolean)
        .map(id => document.getElementById(id)?.textContent || '').join(' ');
    const nearby = [clickable.previousElementSibling, clickable.nextElementSibling]
        .filter(node => node && !node.matches(clickableSelector))
        .map(node => node.textContent || '').find(text => text.trim()) || '';
    return (clickable.getAttribute('aria-label') || labelledBy ||
        clickable.getAttribute('title') ||
        (clickable.labels && Array.from(clickable.labels).map(x => x.textContent).join(' ')) ||
        clickable.innerText || clickable.value ||
        clickable.closest('label')?.textContent || nearby || '')
        .trim().replace(/\s+/g, ' ').slice(0, 120);
}
function positionOf(el) {
    if (!el) return null;
    if (el.matches('iframe, frame'))
        throw new Error('Frame contents are not supported');
    const clickable = el.closest(clickableSelector);
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
    if (hit.closest(clickableSelector) !== clickable) return null;
    return {tag: clickable.tagName.toLowerCase(), x: rect.x, y: rect.y,
            width: rect.width, height: rect.height, label: labelOf(clickable)};
}
function selectorOf(el) {
    const parts = [];
    for (let node = el; node; node = node.parentElement) {
        if (node.id) {
            const byId = '#' + CSS.escape(node.id);
            if (document.querySelectorAll(byId).length === 1) {
                parts.unshift(byId);
                break;
            }
        }
        const siblings = node.parentElement ? Array.from(node.parentElement.children)
            .filter(other => other.tagName === node.tagName) : [node];
        parts.unshift(node.tagName.toLowerCase() + ':nth-of-type(' +
            (siblings.indexOf(node) + 1) + ')');
    }
    const selector = parts.join(' > ');
    const matches = document.querySelectorAll(selector);
    if (matches.length !== 1 || matches[0] !== el)
        throw new Error('Cannot identify the selected element uniquely');
    return selector;
}
function identityOf(el) {
    const attributes = ['id', 'name', 'role', 'type', 'aria-label', 'title']
        .filter(name => el.hasAttribute(name)).map(name => [name, el.getAttribute(name)]);
    // A navigation address may contain authentication data. Retain only its path.
    let navigationPath = null;
    if (el.hasAttribute('href')) {
        const address = new URL(el.getAttribute('href'), location.href);
        if (address.protocol === 'http:' || address.protocol === 'https:') {
            navigationPath = address.pathname;
            attributes.push(['href-path', navigationPath]);
        }
    }
    const uniqueId = !!el.id &&
        document.querySelectorAll('#' + CSS.escape(el.id)).length === 1;
    const uniquePath = navigationPath !== null &&
        Array.from(document.querySelectorAll('a[href]')).filter(anchor => {
            const address = new URL(anchor.getAttribute('href'), location.href);
            return (address.protocol === 'http:' || address.protocol === 'https:') &&
                address.pathname === navigationPath;
        }).length === 1;
    const row = el.closest('tr, li, [role="row"], [role="listitem"]');
    const context = !uniqueId && !uniquePath && row ?
        (row.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 500) : '';
    return {selector: selectorOf(el), tag: el.tagName.toLowerCase(),
            label: labelOf(el), attributes, context};
}
function registeredElement(expected) {
    const matches = document.querySelectorAll(expected.selector);
    if (matches.length > 1) throw new Error('Ambiguous registered element');
    const el = matches[0];
    if (!el || !el.matches(clickableSelector)) return null;
    const actual = identityOf(el);
    if (actual.tag !== expected.tag || actual.label !== expected.label ||
        actual.context !== expected.context ||
        JSON.stringify(actual.attributes) !== JSON.stringify(expected.attributes)) return null;
    return el;
}
"""

_VIDEO_ELEMENT_HELPER = r"""
function lectureVideo() {
    const matches = document.querySelectorAll('#my-video');
    if (matches.length > 1) throw new Error('Ambiguous video player');
    const player = matches[0];
    if (!player) return null;
    if (player instanceof HTMLVideoElement) return player;
    const videos = player.querySelectorAll('video');
    if (videos.length > 1) throw new Error('Ambiguous HTML video inside player');
    return videos[0] || null;
}
"""

_VIDEO_POINT_HELPER = _VIDEO_ELEMENT_HELPER + r"""
function videoPointIsVisible(x, y) {
    const video = lectureVideo();
    if (!video) return false;
    const rect = video.getBoundingClientRect();
    const style = getComputedStyle(video);
    if (style.visibility !== 'visible' || Number(style.opacity) === 0 ||
        rect.width <= 0 || rect.height <= 0 || x < rect.left || x >= rect.right ||
        y < rect.top || y >= rect.bottom || x < 0 || y < 0 ||
        x >= innerWidth || y >= innerHeight) return false;
    const hit = document.elementFromPoint(x, y);
    const player = video.closest('.video-js');
    return !!hit && (hit === video || video.contains(hit) ||
        (!!player && player.contains(hit)));
}
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
        self._binding_name = '__catchcatch_observe_' + uuid4().hex
        self._listener_name = self._binding_name + '_listeners'
        self._binding_installed = False
        self._observation_mode = None
        self._observation_error = None
        self._clicks = deque()
        self._video_captures = deque()
        self._socket.textMessageReceived.connect(self._observe_message)

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
        if self._socket.state() == QAbstractSocket.SocketState.ConnectedState:
            try:
                self._stop_observation()
                if self._binding_installed:
                    self._command('Runtime.removeBinding', {'name': self._binding_name})
            except BrowserReaderError:
                pass
        self._binding_installed = False
        self._socket.abort()

    def _command(self, method, params=None):
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

        payload = json.dumps({'id': request_id, 'method': method, 'params': params or {}})
        response = self._exchange(lambda: self._socket.sendTextMessage(payload),
                                  self._socket.textMessageReceived, accept)
        if 'error' in response:
            raise BrowserReaderError('CDP rejected the DOM query')
        return response.get('result', {})

    def _evaluate(self, expression):
        result = self._command('Runtime.evaluate',
                               {'expression': expression, 'returnByValue': True})
        if 'exceptionDetails' in result:
            details = result['exceptionDetails']
            description = (details.get('exception', {}).get('description')
                           or details.get('text'))
            # Show the exception message, without the JavaScript stack trace.
            if isinstance(description, str) and description.strip():
                reason = description.strip().splitlines()[0][:300]
                raise BrowserReaderError(f'DOM query failed: {reason}')
            raise BrowserReaderError('DOM query failed; check selector and page context')
        remote = result.get('result', {})
        if 'value' not in remote:
            raise BrowserReaderError('CDP returned no query value')
        return remote['value']

    def _position(self, lookup) -> Optional[ElementPosition]:
        value = self._evaluate('(() => {' + _ELEMENT_HELPERS + lookup +
                               'return positionOf(el);})()')
        if value is None:
            return None
        return ElementPosition(**value)

    def device_pixel_ratio(self) -> float:
        """Read the physical-pixel/CSS-pixel ratio, including desktop zoom."""
        value = self._evaluate('window.devicePixelRatio')
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise BrowserReaderError('Browser returned an invalid devicePixelRatio')
        return float(value)

    def page_has_focus(self) -> bool:
        """Whether the selected tab currently owns keyboard focus."""
        return self._evaluate('document.hasFocus()') is True

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

    def identify_clickable_at(self, web_x: float, web_y: float) -> Optional[ElementIdentity]:
        """Hit-test using the existing button geometry and derive a unique identity."""
        if not math.isfinite(web_x) or not math.isfinite(web_y):
            raise ValueError('Coordinates must be finite')
        value = self._evaluate('(() => {' + _ELEMENT_HELPERS +
            'const el = document.elementFromPoint(' + json.dumps(web_x) + ', ' +
            json.dumps(web_y) + '); if (!positionOf(el)) return null;'
            'return identityOf(el.closest(clickableSelector));})()')
        return None if value is None else self._identity(value)

    def find_registered_clickable(self, identity: ElementIdentity) -> Optional[ElementPosition]:
        """Require the saved identity to match, then reuse the existing button query."""
        payload = json.dumps(asdict(identity))
        return self._position('const el = registeredElement(' + payload + ');')

    def scroll_registered_into_view(self, identity: ElementIdentity) -> bool:
        """The user-requested scroll, only after an exact identity match."""
        payload = json.dumps(asdict(identity))
        return self._evaluate('(() => {' + _ELEMENT_HELPERS +
            'const el = registeredElement(' + payload + '); if (!el) return false;'
            'el.scrollIntoView({block: "center", inline: "nearest", behavior: "instant"});'
            'return true;})()') is True

    def page_snapshot(self) -> PageSnapshot:
        """Read geometry and the current URL without storing browser/authentication data."""
        value = self._evaluate('({url: location.href, has_focus: document.hasFocus(),'
            'screen_x: window.screenX, screen_y: window.screenY,'
            'outer_width: window.outerWidth, outer_height: window.outerHeight,'
            'viewport_width: window.innerWidth, viewport_height: window.innerHeight,'
            'device_pixel_ratio: window.devicePixelRatio})')
        if (not isinstance(value, dict) or not isinstance(value.get('url'), str)
                or not isinstance(value.get('has_focus'), bool)):
            raise BrowserReaderError('Invalid browser page snapshot')
        try:
            snapshot = PageSnapshot(**value)
        except TypeError as error:
            raise BrowserReaderError('Invalid browser page snapshot') from error
        if (any(isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number) for number in snapshot.geometry)
                or min(snapshot.outer_width, snapshot.outer_height,
                       snapshot.viewport_width, snapshot.viewport_height,
                       snapshot.device_pixel_ratio) <= 0):
            raise BrowserReaderError('Invalid browser window geometry')
        return snapshot

    def video_point_is_visible(self, web_x: float, web_y: float) -> bool:
        """Verify the saved point still hits the visible video or Video.js controls."""
        if not math.isfinite(web_x) or not math.isfinite(web_y):
            raise ValueError('Coordinates must be finite')
        return self._evaluate('(() => {' + _VIDEO_POINT_HELPER +
            'return videoPointIsVisible(' + json.dumps(web_x) + ', ' +
            json.dumps(web_y) + ');})()') is True

    @staticmethod
    def _identity(value):
        if not isinstance(value, dict):
            raise BrowserReaderError('Invalid captured element identity')
        try:
            attributes = tuple(tuple(attribute) for attribute in value['attributes'])
            identity = ElementIdentity(value['selector'], value['tag'], value['label'],
                                       attributes, value['context'])
        except (KeyError, TypeError, ValueError) as error:
            raise BrowserReaderError('Invalid captured element identity') from error
        if (not all(isinstance(text, str) for text in
                    (identity.selector, identity.tag, identity.label, identity.context))
                or not identity.selector or not identity.tag
                or any(len(attribute) != 2 or not all(isinstance(text, str) for text in attribute)
                       for attribute in attributes)):
            raise BrowserReaderError('Invalid captured element identity')
        return identity

    def _observe_message(self, message):
        """Receive observation events independently of synchronous CDP replies."""
        try:
            event = json.loads(message)
            if not isinstance(event, dict):
                return
            if event.get('method') != 'Runtime.bindingCalled':
                return
            params = event.get('params', {})
            if params.get('name') != self._binding_name or self._observation_mode is None:
                return
            payload = json.loads(params.get('payload', ''))
            if not isinstance(payload, dict):
                raise ValueError('Invalid observation payload')
            if payload.get('mode') != self._observation_mode:
                return
            if payload.get('error'):
                self._observation_error = '등록 위치를 확인할 수 없습니다. 커서를 다시 움직인 뒤 시도하세요.'
                return
            if self._observation_mode == 'click':
                identity = self._identity(payload['identity'])
                position = ElementPosition(**payload['position'])
                if not all(isinstance(number, (int, float)) and not isinstance(number, bool)
                           and math.isfinite(number) for number in
                           (position.x, position.y, position.width, position.height)):
                    raise ValueError('Invalid click position')
                self._clicks.append(ClickedElement(identity, position))
            elif self._observation_mode == 'video':
                capture = VideoCapture(payload['web_x'], payload['web_y'], payload['duration'])
                if (not all(isinstance(number, (int, float)) and not isinstance(number, bool)
                            and math.isfinite(number) for number in
                            (capture.web_x, capture.web_y, capture.duration)) or capture.duration <= 0):
                    raise ValueError('Invalid video capture')
                self._video_captures.append(capture)
            if len(self._clicks) + len(self._video_captures) > 100:
                self._clicks.clear()
                self._video_captures.clear()
                self._observation_error = '등록 입력이 너무 많습니다. 영상 등록을 다시 시작하세요.'
        except (KeyError, TypeError, ValueError, BrowserReaderError):
            self._observation_error = '영상 등록 입력을 확인할 수 없습니다.'

    def _start_observation(self, mode):
        if self._observation_mode is not None:
            self._stop_observation()
        if not self._binding_installed:
            self._command('Runtime.enable')
            self._command('Runtime.addBinding', {'name': self._binding_name})
            self._binding_installed = True
        self._observation_error = None
        self._clicks.clear()
        self._video_captures.clear()
        self._observation_mode = mode
        script = '(() => {' + _ELEMENT_HELPERS + _VIDEO_POINT_HELPER + r'''
            const key = LISTENER_KEY;
            const mode = OBSERVATION_MODE;
            const send = value => window[BINDING_NAME](JSON.stringify({mode, ...value}));
            const previous = window[key];
            if (previous) for (const [kind, listener] of previous)
                document.removeEventListener(kind, listener, true);
            const listeners = [];
            const add = (kind, listener) => {
                document.addEventListener(kind, listener, true);
                listeners.push([kind, listener]);
            };
            if (mode === 'click') {
                add('click', event => {
                    if (!event.isTrusted || event.button !== 0 || !document.hasFocus()) return;
                    try {
                        const el = event.target instanceof Element ? event.target : null;
                        const position = positionOf(el);
                        if (!position) return;
                        send({identity: identityOf(el.closest(clickableSelector)), position});
                    } catch (_) { send({error: true}); }
                });
            } else {
                let pointer = null;
                const moved = event => {
                    if (event.isTrusted)
                        pointer = {x: event.clientX, y: event.clientY, time: performance.now()};
                };
                add('pointermove', moved);
                add('mousemove', moved);
                add('keyup', event => {
                    if (!event.isTrusted || event.code !== 'Space' || !document.hasFocus()) return;
                    try {
                        const video = lectureVideo();
                        if (!video || !pointer || performance.now() - pointer.time > 30000 ||
                            !videoPointIsVisible(pointer.x, pointer.y) ||
                            !Number.isFinite(video.duration) || video.duration <= 0) {
                            send({error: true}); return;
                        }
                        send({web_x: pointer.x, web_y: pointer.y, duration: video.duration});
                    } catch (_) { send({error: true}); }
                });
            }
            window[key] = listeners;
            return true;
        })();'''
        script = script.replace('LISTENER_KEY', json.dumps(self._listener_name))
        script = script.replace('OBSERVATION_MODE', json.dumps(mode))
        script = script.replace('BINDING_NAME', json.dumps(self._binding_name))
        try:
            self._evaluate(script)
        except BrowserReaderError:
            self._observation_mode = None
            raise

    def _stop_observation(self):
        active = self._observation_mode is not None
        self._observation_mode = None
        self._clicks.clear()
        self._video_captures.clear()
        self._observation_error = None
        if active:
            key = json.dumps(self._listener_name)
            self._evaluate('(() => { const key = ' + key + '; const listeners = window[key];'
                'if (listeners) for (const [kind, listener] of listeners)'
                'document.removeEventListener(kind, listener, true);'
                'delete window[key]; return true; })()')

    def observation_is_active(self) -> bool:
        """A reload destroys listeners, so inspect the current document before reusing them."""
        if self._observation_mode is None:
            return False
        return self._evaluate('!!window[' + json.dumps(self._listener_name) + ']') is True

    def start_click_observation(self):
        """Observe normal trusted list clicks without intercepting user input."""
        self._start_observation('click')

    def stop_click_observation(self):
        self._stop_observation()

    def take_clicked_identity(self) -> Optional[ClickedElement]:
        if self._observation_error:
            error, self._observation_error = self._observation_error, None
            raise BrowserReaderError(error)
        return self._clicks.popleft() if self._clicks else None

    def start_video_observation(self):
        """Observe the user's Space release and pointer at the current video."""
        self._start_observation('video')

    def stop_video_observation(self):
        self._stop_observation()

    def take_video_capture(self) -> Optional[VideoCapture]:
        if self._observation_error:
            error, self._observation_error = self._observation_error, None
            raise BrowserReaderError(error)
        return self._video_captures.popleft() if self._video_captures else None
