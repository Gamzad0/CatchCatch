from collections.abc import Iterable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class _StatusLabel(QLabel):
    """Forward controller status changes to the current action prompt."""

    text_updated = Signal(str)

    def setText(self, text: str) -> None:
        super().setText(text)
        self.text_updated.emit(text)


class MainWindow(QWidget):
    queue_wait_changed = Signal(int, int)
    review_visibility_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CatchCatch")
        self.resize(520, 520)
        self.setMinimumSize(380, 420)
        self._state = "idle"
        self._has_queue = False
        self._playback_ready = False
        self._browser_connected = False
        self._calibration_ready = False
        self._review_visible = False
        self._queue_entries = []
        self._queue_editable = True
        self._playback_total = 0
        self._completed = False
        self._error_message = ""
        self._setup_message = "전용 Chrome을 열고 연결합니다."

        self.browser_button = QPushButton("브라우저 연결")
        self.calibrate_button = QPushButton("다시 보정")
        self.coordinate_test_button = QPushButton("좌표 변환 테스트")
        self.register_button = QPushButton("강의 등록")
        self.finish_registration_button = QPushButton("재생 시작")
        self.start_button = QPushButton("재생 시작")
        self.stop_button = QPushButton("중지")
        self.clear_queue_button = QPushButton("목록 비우기")

        # Controllers publish status here; the screen shows one relevant prompt.
        self.browser_status = _StatusLabel(self._setup_message, self)
        self.cursor_status = _StatusLabel("보정이 필요합니다.", self)
        self.registration_status = _StatusLabel("강의 목록에서 시청할 강의를 선택하세요.", self)
        self.playback_status = _StatusLabel("재생 대기", self)
        for label in (self.browser_status, self.registration_status, self.playback_status):
            label.hide()
        self._readiness_message = "강의 목록을 확인하는 중입니다…"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        header = QHBoxLayout()
        brand = QLabel("CatchCatch")
        font = brand.font()
        font.setBold(True)
        brand.setFont(font)
        header.addWidget(brand)
        header.addStretch()
        self.settings_button = QToolButton()
        self.settings_button.setText("설정")
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        settings_menu = QMenu(self.settings_button)
        self.reconnect_action = settings_menu.addAction("브라우저 다시 연결")
        self.recalibrate_action = settings_menu.addAction("다시 보정")
        settings_menu.addSeparator()
        self.diagnostics_action = settings_menu.addAction("진단 정보")
        self.diagnostics_action.setCheckable(True)
        self.settings_button.setMenu(settings_menu)
        header.addWidget(self.settings_button)
        layout.addLayout(header)

        self.action_title = self._label("")
        font = self.action_title.font()
        font.setPointSize(18)
        font.setBold(True)
        self.action_title.setFont(font)
        self.action_hint = self._label("")
        self.action_hint.setMinimumHeight(40)
        layout.addWidget(self.action_title)
        layout.addWidget(self.action_hint)

        self.pages = QStackedWidget()
        self.setup_page = QWidget()
        self.pages.addWidget(self.setup_page)
        self.queue_page = QWidget()
        queue_layout = QVBoxLayout(self.queue_page)
        queue_layout.setContentsMargins(0, 0, 0, 0)
        queue_layout.setSpacing(10)
        summary = QHBoxLayout()
        self.queue_summary = QLabel("등록한 강의 0개")
        self.playback_progress = QLabel("")
        summary.addWidget(self.queue_summary)
        summary.addStretch()
        summary.addWidget(self.playback_progress)
        queue_layout.addLayout(summary)
        self.queue_list = QListWidget()
        self.queue_list.setSpacing(4)
        self.queue_wait_inputs = []
        queue_layout.addWidget(self.queue_list, 1)
        self.pages.addWidget(self.queue_page)
        layout.addWidget(self.pages, 1)

        self.diagnostics_panel = QGroupBox("진단 정보")
        diagnostics_layout = QVBoxLayout(self.diagnostics_panel)
        self.cursor_status.setWordWrap(True)
        diagnostics_layout.addWidget(self.cursor_status)
        diagnostics_layout.addWidget(self.coordinate_test_button)
        self.diagnostics_panel.hide()
        layout.addWidget(self.diagnostics_panel)

        actions = QHBoxLayout()
        actions.addWidget(self.clear_queue_button)
        actions.addStretch()
        actions.addWidget(self.stop_button)
        actions.addWidget(self.register_button)
        for button in (
            self.browser_button, self.calibrate_button,
            self.finish_registration_button, self.start_button,
        ):
            actions.addWidget(button)
            font = button.font()
            font.setBold(True)
            button.setFont(font)
        for button in (
            self.browser_button, self.calibrate_button, self.register_button,
            self.finish_registration_button, self.start_button,
            self.stop_button, self.clear_queue_button,
        ):
            button.setMinimumHeight(40)
        layout.addLayout(actions)

        self.reconnect_action.triggered.connect(self.browser_button.click)
        self.recalibrate_action.triggered.connect(self.calibrate_button.click)
        self.diagnostics_action.toggled.connect(self.diagnostics_panel.setVisible)
        settings_menu.aboutToShow.connect(self._update_settings)
        self.browser_button.clicked.connect(self._connection_requested)
        self.calibrate_button.clicked.connect(self._calibration_requested)
        for button in (self.register_button, self.finish_registration_button, self.start_button):
            button.clicked.connect(self._action_requested)
        self.browser_status.text_updated.connect(self._browser_status_updated)
        self.cursor_status.text_updated.connect(self._cursor_status_updated)
        self.registration_status.text_updated.connect(lambda _: self._refresh_ui())
        self.playback_status.text_updated.connect(self._playback_status_updated)
        self.set_workflow_state("idle", has_queue=False)

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def _refresh_ui(self) -> None:
        registering = self._state in {"registering", "waiting_list", "pending_player"}
        playing = self._state == "playing"
        active = registering or playing
        ready = self._state in {"idle", "stopped"}
        setup = not self._browser_connected or not self._calibration_ready
        self.pages.setCurrentWidget(self.setup_page if setup else self.queue_page)
        self.settings_button.setEnabled(not active)
        self.browser_button.setVisible(setup and not self._browser_connected)
        self.calibrate_button.setVisible(setup and self._browser_connected)
        self.register_button.setVisible(not setup and ready)
        self.register_button.setText("강의 추가" if self._has_queue else "강의 등록")
        self.register_button.setEnabled(ready and not setup)
        self.finish_registration_button.setVisible(not setup and registering and self._has_queue)
        self.start_button.setVisible(not setup and ready and self._has_queue)
        can_start = self._has_queue and self._playback_ready and not setup
        self.finish_registration_button.setEnabled(
            can_start and self._state in {"registering", "waiting_list"}
        )
        self.start_button.setEnabled(can_start and ready)
        self.stop_button.setVisible(active)
        self.stop_button.setText("등록 중지" if registering else "중지")
        self.clear_queue_button.setVisible(not setup and ready and self._has_queue)
        self.playback_progress.setVisible(playing or self._completed)

        if setup:
            title = "화면을 보정하세요" if self._browser_connected else "브라우저를 연결하세요"
            message = self._setup_message
        elif self._error_message:
            title, message = "상태를 확인하세요", self._error_message
        elif playing:
            title, message = "강의 재생 중", self.playback_status.text()
        elif self._completed:
            title, message = "모든 강의를 재생했습니다", "다시 시청하려면 강의를 등록하세요."
        elif registering:
            title, message = "강의 등록 중", self.registration_status.text()
        elif self._has_queue:
            title, message = "등록한 강의", self._readiness_message
        else:
            title = "강의를 등록하세요"
            message = "LMS의 원하는 과목에서 'n주차' 옆 '자세히 보기' 클릭 후 ‘강의 등록’을 누르세요."
        self.action_title.setText(title)
        self.action_hint.setText(message)

        # Observe start readiness on the same screen as registration and playback.
        review_visible = not setup and self._has_queue and not playing and self._state != "pending_player"
        if review_visible != self._review_visible:
            self._review_visible = review_visible
            self.review_visibility_changed.emit(review_visible)

    def _update_settings(self) -> None:
        self.reconnect_action.setEnabled(self.browser_button.isEnabled())
        self.recalibrate_action.setEnabled(
            self._browser_connected and self.calibrate_button.isEnabled()
        )

    def _action_requested(self) -> None:
        self._error_message = ""
        self._completed = False
        self._refresh_ui()

    def _connection_requested(self) -> None:
        self._browser_connected = False
        self._calibration_ready = False
        self._setup_message = "Chrome에 연결하는 중입니다…"
        self._refresh_ui()

    def _calibration_requested(self) -> None:
        self._calibration_ready = False
        self._setup_message = "보정 화면을 준비하는 중입니다…"
        self._refresh_ui()

    def _browser_status_updated(self, text: str) -> None:
        if text.startswith(("브라우저 연결 완료.", "전용 Chrome이 연결되어 있습니다.")):
            self._browser_connected = True
            self._setup_message = "보정 화면을 준비하는 중입니다…"
        elif text.startswith("보정 완료."):
            self._browser_connected = True
            self._calibration_ready = True
            self._error_message = ""
        elif text.startswith(("브라우저 연결이 끊겼습니다.", "Chrome에 연결하지 못했습니다.")):
            self._browser_connected = False
            self._calibration_ready = False
            self._setup_message = text
        else:
            self._setup_message = text
            if self._calibration_ready:
                self._error_message = text
        self._refresh_ui()

    def _cursor_status_updated(self, text: str) -> None:
        if text.startswith((
            "보정 실패:", "커서 감지 중지:", "보정 화면이 열리지 않았습니다.",
            "보정 화면을 열지 못했습니다:", "보정 화면 탐색 실패:", "초기 화면 start.html",
        )):
            self._calibration_ready = False
            self._setup_message = text
        elif text.startswith(("보정 화면", "Chrome은 왼쪽", "창 배치 완료.")):
            self._calibration_ready = False
            self._setup_message = text
        elif not self._calibration_ready:
            self._setup_message = text
        else:
            return
        self._refresh_ui()

    def _playback_status_updated(self, text: str) -> None:
        if text.startswith("중지:"):
            self._error_message = text.removeprefix("중지:").strip()
        elif text == "모든 영상의 순차 재생을 완료했습니다.":
            self._error_message = ""
            self._completed = True
        self._refresh_ui()

    def set_playback_ready(self, ready: bool, message: str) -> None:
        """Only the controller's page observation may enable playback."""
        self._playback_ready = ready
        self._readiness_message = message
        self._refresh_ui()

    def set_queue(self, entries: Iterable[tuple[str, int]]) -> None:
        items = list(entries)
        self._queue_entries = items
        self._has_queue = bool(items)
        self._playback_ready = False
        self._completed = False
        if not items:
            self._error_message = ""
        self.queue_list.clear()
        self.queue_wait_inputs.clear()
        for index, (text, wait_seconds) in enumerate(items):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 8, 8, 8)
            label = self._label(text)
            row_layout.addWidget(label, 1)
            wait_input = QSpinBox()
            wait_input.setRange(1, 2147483647)
            wait_input.setSuffix("초")
            wait_input.setValue(wait_seconds)
            wait_input.setAccessibleName(f"{index + 1}번째 강의 전환 대기 시간(초)")
            wait_input.setToolTip("재생 시작부터 다음 강의 전환까지의 시간. 기본값: 영상 길이 + 180초")
            wait_input.setEnabled(self._queue_editable)
            wait_input.valueChanged.connect(
                lambda value, row_index=index: self.queue_wait_changed.emit(row_index, value)
            )
            wait_layout = QVBoxLayout()
            wait_layout.setSpacing(4)
            wait_layout.addWidget(QLabel("전환 대기"))
            wait_layout.addWidget(wait_input)
            row_layout.addLayout(wait_layout)
            item = QListWidgetItem(self.queue_list)
            item.setSizeHint(row.sizeHint())
            self.queue_list.setItemWidget(item, row)
            self.queue_wait_inputs.append(wait_input)
        self.queue_summary.setText(f"등록한 강의 {len(items)}개")
        self._update_playback_summary()
        self._refresh_ui()

    def _update_playback_summary(self) -> None:
        remaining = len(self._queue_entries)
        completed = max(0, self._playback_total - remaining)
        self.playback_progress.setText(f"{completed} / {self._playback_total}개 완료")
        if self._state == "playing" and remaining:
            self.queue_list.setCurrentRow(0)

    def set_workflow_state(self, state: str, has_queue: bool) -> None:
        """Keep input controls aligned with the controller's actual state."""
        previous_state = self._state
        self._state = state
        self._has_queue = has_queue
        self._playback_ready = False
        self._readiness_message = "강의 목록을 확인하는 중입니다…"
        playing = state == "playing"
        active = state in {"registering", "waiting_list", "pending_player", "playing"}
        self._queue_editable = not playing
        for wait_input in self.queue_wait_inputs:
            wait_input.setEnabled(self._queue_editable)
        self.browser_button.setEnabled(not active)
        self.calibrate_button.setEnabled(not active)
        self.coordinate_test_button.setEnabled(not active)
        self.stop_button.setEnabled(active)
        self.clear_queue_button.setEnabled(not active and has_queue)
        if active:
            self._error_message = ""
            self._completed = False
        if playing and previous_state != "playing":
            self._playback_total = len(self._queue_entries)
        self._update_playback_summary()
        self._refresh_ui()
