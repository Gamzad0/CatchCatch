from collections.abc import Iterable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class _StatusLabel(QLabel):
    """Mirror the controller's existing status updates into the step UI."""

    text_updated = Signal(str)

    def setText(self, text: str) -> None:
        super().setText(text)
        self.text_updated.emit(text)


class MainWindow(QWidget):
    queue_wait_changed = Signal(int, int)
    review_visibility_changed = Signal(bool)
    _STEP_NAMES = ("브라우저 연결", "좌표 보정", "강의 등록", "목록 확인", "자동 재생")

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CatchCatch")
        self.resize(640, 600)
        self._step = 0
        self._state = "idle"
        self._has_queue = False
        self._playback_ready = False
        self._browser_connected = False
        self._calibration_ready = False
        self._queue_entries = []
        self._playback_total = 0

        # Keep the controller-facing buttons and signals intact. Registration
        # completion is invoked only from step 4, after the user reviews the queue.
        self.browser_button = QPushButton("브라우저 연결")
        self.coordinate_test_button = QPushButton("좌표 변환 테스트")
        self.register_button = QPushButton("등록 시작")
        self.finish_registration_button = QPushButton("자동 재생 시작")
        self.start_button = QPushButton("자동 재생 시작")
        self.stop_button = QPushButton("중지")
        self.clear_queue_button = QPushButton("목록 비우기")
        self.back_button = QPushButton("이전")
        self.next_button = QPushButton("다음")
        self.review_button = QPushButton("등록 완료")
        self.add_lectures_button = QPushButton("강의 추가")
        self.new_registration_button = QPushButton("새 강의 등록")
        self.browser_status = _StatusLabel("브라우저를 연결하면 보정 화면이 열립니다.")
        self.calibration_status = QLabel("브라우저 연결 후 보정이 자동으로 시작됩니다.")
        self.cursor_status = _StatusLabel("커서 아래 버튼: 보정이 필요합니다.")
        self.registration_status = _StatusLabel("영상 등록 대기")
        self.playback_status = _StatusLabel("자동 재생 대기")
        self.review_status = QLabel("등록 순서와 총 대기 시간을 확인하세요.")
        self.playback_readiness_status = QLabel("강의 목록을 확인하는 중입니다…")
        self.registration_count = QLabel("등록된 강의: 0개")
        self.latest_registration = QLabel("아직 등록된 강의가 없습니다.")
        self.playback_current = QLabel("재생할 강의가 없습니다.")
        self.playback_progress = QLabel("완료 0 / 전체 0")
        self.queue_summary = QLabel("등록된 강의: 0개")
        self.queue_order_instructions = QLabel("등록한 순서대로 재생합니다.")
        self.queue_list = QListWidget()
        self.queue_wait_inputs = []
        self._queue_editable = True

        self.calibration_instructions = self._label(
            "Chrome은 화면 왼쪽, 앱은 오른쪽에 배치됩니다. 창 배치가 완료되면 "
            "5초 카운트다운이 시작됩니다. "
            "Chrome 첫 페이지의 빈 곳을 클릭한 뒤, 5초 안에 '보정 기준' 버튼의 "
            "빨간 + 중앙에 커서를 놓고 유지하세요. 시간을 놓쳤다면 다시 보정하세요. "
            "보정 후에는 브라우저 위치·크기·확대 비율을 유지하세요."
        )
        self.calibrate_button = QPushButton("다시 보정")
        self.registration_instructions = self._label(
            "'등록 시작'을 누른 뒤 LMS 강의 목록에서 시청할 항목을 클릭하세요. "
            "같은 탭에서 영상 정보가 준비되면 자동으로 등록합니다. "
            "등록 완료 안내를 확인한 뒤 직접 강의 목록으로 돌아가 다음 강의를 선택하세요. "
            "모두 등록했다면 강의 목록으로 돌아와 '등록 완료'를 누르세요. "
            "다음 화면에서 목록과 대기 시간을 확인한 뒤 자동 재생을 시작합니다. "
            "등록 정보는 앱 실행 중 메모리에만 보관합니다."
        )

        for label in (
            self.browser_status,
            self.calibration_status,
            self.cursor_status,
            self.registration_status,
            self.playback_status,
            self.review_status,
            self.playback_readiness_status,
            self.registration_count,
            self.latest_registration,
            self.playback_current,
            self.playback_progress,
            self.queue_summary,
        ):
            font = label.font()
            font.setBold(True)
            label.setFont(font)
            label.setWordWrap(True)
            label.setContentsMargins(0, 6, 0, 6)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        steps = QHBoxLayout()
        self.step_labels = []
        for index, name in enumerate(("연결", "보정", "등록", "확인", "재생")):
            label = self._label(f"{index + 1}. {name}")
            label.setStyleSheet("padding: 8px 4px; border-bottom: 2px solid #b5b5b5;")
            steps.addWidget(label, 1)
            self.step_labels.append(label)
        layout.addLayout(steps)
        self.step_title = self._label("")
        title_font = self.step_title.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        self.step_title.setFont(title_font)
        self.step_hint = self._label("")
        layout.addWidget(self.step_title)
        layout.addWidget(self.step_hint)

        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        connection_page = self._page_layout(self.browser_status)
        connection_page.addStretch()
        calibration_page = self._page_layout(self.calibration_status, self.calibrate_button)
        calibration_page.addStretch()
        registration_page = self._page_layout(
            self.registration_status, self.registration_count, self.latest_registration
        )
        registration_page.addStretch()
        review_page = self._page_layout(
            self.queue_summary, self.queue_order_instructions, self.queue_list,
            self.playback_readiness_status, self.review_status,
        )
        queue_actions = QHBoxLayout()
        queue_actions.addWidget(self.add_lectures_button)
        queue_actions.addWidget(self.clear_queue_button)
        queue_actions.addStretch()
        review_page.addLayout(queue_actions)
        playback_page = self._page_layout(
            self.playback_current, self.playback_progress, self.playback_status
        )
        playback_page.addStretch()

        details = QHBoxLayout()
        self.help_toggle = QToolButton()
        self.help_toggle.setText("도움말")
        self.help_toggle.setCheckable(True)
        self.diagnostics_toggle = QToolButton()
        self.diagnostics_toggle.setText("진단")
        self.diagnostics_toggle.setCheckable(True)
        details.addWidget(self.help_toggle)
        details.addStretch()
        details.addWidget(self.diagnostics_toggle)
        layout.addLayout(details)
        self.help_pages = QStackedWidget()
        for label in (
            self._label("전용 Chrome이 열리면 연결 상태를 확인합니다. 기존 Chrome과 구분해 사용하세요."),
            self.calibration_instructions,
            self.registration_instructions,
            self._label(
                "총 대기는 재생 시작 확인부터 다음 강의 전환까지의 시간입니다. "
                "기본값은 영상 길이 + 180초이며 영상별로 수정할 수 있습니다. "
                "등록 중 목록을 비우려면 먼저 중지하세요. "
                "검토 중에도 강의를 열면 추가 등록될 수 있으므로 목록 화면을 유지하세요."
            ),
            self._label(
                "보정한 Chrome 탭을 사용하고 창 위치·크기·확대 비율을 유지하세요. "
                "자동 재생은 플레이어 중앙을 클릭합니다. 중지한 강의는 목록에 유지되며, "
                "강의 목록으로 돌아간 뒤 다시 시작할 수 있습니다."
            ),
        ):
            self.help_pages.addWidget(label)
        self.help_pages.hide()
        layout.addWidget(self.help_pages)
        self.diagnostics_panel = QGroupBox("진단 정보")
        diagnostics_layout = QVBoxLayout(self.diagnostics_panel)
        diagnostics_layout.addWidget(self.cursor_status)
        diagnostics_layout.addWidget(self.coordinate_test_button)
        self.diagnostics_panel.hide()
        layout.addWidget(self.diagnostics_panel)

        actions = QHBoxLayout()
        actions.addWidget(self.back_button)
        actions.addStretch()
        actions.addWidget(self.stop_button)
        for button in (
            self.browser_button, self.next_button, self.register_button, self.review_button,
            self.finish_registration_button, self.start_button, self.new_registration_button,
        ):
            actions.addWidget(button)
            font = button.font()
            font.setBold(True)
            button.setFont(font)
        layout.addLayout(actions)

        self.help_toggle.toggled.connect(self.help_pages.setVisible)
        self.diagnostics_toggle.toggled.connect(self.diagnostics_panel.setVisible)
        self.back_button.clicked.connect(self._go_back)
        self.next_button.clicked.connect(lambda: self._show_step(2))
        self.review_button.clicked.connect(self._review_queue)
        self.add_lectures_button.clicked.connect(lambda: self._show_step(2))
        self.new_registration_button.clicked.connect(self._show_registration)
        self.browser_button.clicked.connect(self._connection_requested)
        self.calibrate_button.clicked.connect(self._calibration_requested)
        self.browser_status.text_updated.connect(self._browser_status_updated)
        self.cursor_status.text_updated.connect(self._cursor_status_updated)
        self.registration_status.text_updated.connect(self.review_status.setText)
        self.playback_status.text_updated.connect(self._playback_status_updated)
        self.set_workflow_state("idle", has_queue=False)
        self._show_step(0)

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def _page_layout(self, *widgets: QWidget) -> QVBoxLayout:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        for widget in widgets:
            layout.addWidget(widget)
        self.pages.addWidget(page)
        return layout

    def _show_step(self, step: int) -> None:
        if self._state == "playing" and step != 4:
            return
        previous_step = self._step
        self._step = step
        if previous_step != step:
            self._playback_ready = False
        self.pages.setCurrentIndex(step)
        self.help_pages.setCurrentIndex(step)
        self.help_toggle.setChecked(False)
        self.diagnostics_toggle.setChecked(False)
        self.step_title.setText(f"{step + 1}단계 · {self._STEP_NAMES[step]}")
        self.step_hint.setText((
            "전용 Chrome을 연결하세요. 연결되면 좌표 보정으로 이동합니다.",
            "Chrome 페이지를 활성화한 뒤 빨간 + 중앙에 커서를 놓고 유지하세요.",
            "보정한 탭에서 LMS 강의 목록을 열고, 등록 시작 후 강의를 차례로 선택하세요.",
            "등록 순서와 총 대기 시간을 확인한 뒤 브라우저를 강의 목록으로 돌려놓으세요.",
            "현재 강의와 진행 상태를 확인하세요. 필요하면 중지할 수 있습니다.",
        )[step])
        for index, label in enumerate(self.step_labels):
            if index == step:
                label.setStyleSheet(
                    "padding: 8px 4px; border-bottom: 3px solid #4385d3; font-weight: bold;"
                )
            else:
                label.setStyleSheet("padding: 8px 4px; border-bottom: 2px solid #b5b5b5;")
        self._update_step_controls()
        if (previous_step == 3) != (step == 3):
            self.review_visibility_changed.emit(step == 3)

    def _update_step_controls(self) -> None:
        registering = self._state in {"registering", "waiting_list", "pending_player"}
        active = registering or self._state == "playing"
        self.back_button.setVisible(self._step in {1, 2, 3})
        self.back_button.setEnabled(not active or (registering and self._step == 3))
        self.browser_button.setVisible(self._step == 0)
        self.next_button.setVisible(self._step == 1)
        self.next_button.setEnabled(self._calibration_ready and not active)
        self.register_button.setVisible(self._step == 2 and not registering)
        self.review_button.setVisible(self._step == 2)
        self.review_button.setEnabled(
            self._has_queue and self._state != "pending_player" and self._calibration_ready
        )
        self.finish_registration_button.setVisible(self._step == 3 and registering)
        self.start_button.setVisible(self._step == 3 and not registering)
        can_start = (
            self._step == 3 and self._has_queue and self._playback_ready
            and self._browser_connected and self._calibration_ready
        )
        self.finish_registration_button.setEnabled(
            can_start and self._state in {"registering", "waiting_list"}
        )
        self.start_button.setEnabled(can_start and self._state in {"idle", "stopped"})
        self.stop_button.setVisible(active)
        self.add_lectures_button.setEnabled(self._state != "playing")
        self.clear_queue_button.setToolTip("등록 중에는 먼저 중지한 뒤 목록을 비우세요.")
        self.new_registration_button.setVisible(self._step == 4 and not active)

    def set_playback_ready(self, ready: bool, message: str) -> None:
        """Only the controller's page observation may enable a start action."""
        self._playback_ready = ready
        self.playback_readiness_status.setText(message)
        self._update_step_controls()

    def _go_back(self) -> None:
        if self.back_button.isEnabled():
            self._show_step(self._step - 1)

    def _review_queue(self) -> None:
        if not self.review_button.isEnabled():
            return
        self.review_status.setText("총 대기 시간은 영상별로 수정할 수 있습니다.")
        # Navigation only: the controller's finish action also starts playback,
        # so its existing signal belongs to the explicit start button in step 4.
        self._show_step(3)

    def _show_setup(self) -> None:
        self._show_step(1 if self._browser_connected else 0)

    def _show_registration(self) -> None:
        if self._calibration_ready:
            self._show_step(2)
        else:
            self._show_setup()

    def _connection_requested(self) -> None:
        self._browser_connected = False
        self._calibration_ready = False
        self._show_step(0)

    def _calibration_requested(self) -> None:
        self._calibration_ready = False
        self.calibration_status.setText("보정 화면을 준비하는 중입니다…")
        self._show_step(1)

    def _browser_status_updated(self, text: str) -> None:
        # The unchanged controller exposes readiness through these status labels.
        if text.startswith(("브라우저 연결 완료.", "전용 Chrome이 연결되어 있습니다.")):
            self._browser_connected = True
            if self._step == 0:
                self._show_step(1)
        elif text.startswith("보정 완료."):
            self._browser_connected = True
            self._calibration_ready = True
            self.calibration_status.setText("좌표 보정 완료. 다음을 눌러 강의를 등록하세요.")
        elif text.startswith(("브라우저 연결이 끊겼습니다.", "Chrome에 연결하지 못했습니다.")):
            self._browser_connected = False
            self._calibration_ready = False
            self._show_step(0)
        self._update_step_controls()

    def _cursor_status_updated(self, text: str) -> None:
        if text.startswith((
            "보정 실패:", "커서 감지 중지:", "보정 화면이 열리지 않았습니다.",
            "보정 화면을 열지 못했습니다:", "보정 화면 탐색 실패:", "초기 화면 start.html",
        )):
            self._calibration_ready = False
            self.calibration_status.setText(text)
            if self._state not in {"registering", "waiting_list", "pending_player", "playing"}:
                self.calibrate_button.setEnabled(True)
                self._show_setup()
        elif text.startswith(("보정 화면", "Chrome은 왼쪽", "창 배치 완료.")):
            self._calibration_ready = False
            self.calibration_status.setText(text)
        elif not self._calibration_ready:
            self.calibration_status.setText(text)
        self._update_step_controls()

    def _playback_status_updated(self, text: str) -> None:
        if text.startswith("중지:"):
            self.review_status.setText(text)
            if self._step == 2:
                self.registration_status.setText(text)
        elif text == "모든 영상의 순차 재생을 완료했습니다.":
            self._show_step(4)
            self.step_hint.setText("등록한 모든 강의의 순차 재생을 완료했습니다.")
            self.playback_current.setText("전체 재생 완료")

    def set_queue(self, entries: Iterable[tuple[str, int]]) -> None:
        """Show each label beside its total wait editor, in seconds."""
        items = list(entries)
        self._queue_entries = items
        self._has_queue = bool(items)
        self.queue_list.clear()
        self.queue_wait_inputs.clear()
        for index, (text, wait_seconds) in enumerate(items):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(6, 4, 6, 4)
            label = QLabel(text)
            label.setWordWrap(True)
            font = label.font()
            font.setBold(True)
            label.setFont(font)
            row_layout.addWidget(label, 1)
            wait_layout = QVBoxLayout()
            wait_layout.addWidget(QLabel("총 대기"))
            wait_input = QSpinBox()
            font = wait_input.font()
            font.setBold(True)
            wait_input.setFont(font)
            wait_input.setRange(1, 2147483647)
            wait_input.setSuffix("초")
            wait_input.setValue(wait_seconds)
            wait_input.setAccessibleName(f"{index + 1}번째 영상 총 대기 시간(초)")
            wait_input.setToolTip("재생 시작 확인부터 다음 영상 전환까지의 총 대기 시간(초)")
            wait_input.setEnabled(self._queue_editable)
            wait_input.valueChanged.connect(
                lambda value, row_index=index: self.queue_wait_changed.emit(row_index, value)
            )
            wait_layout.addWidget(wait_input)
            row_layout.addLayout(wait_layout)
            item = QListWidgetItem(self.queue_list)
            item.setSizeHint(row.sizeHint())
            self.queue_list.setItemWidget(item, row)
            self.queue_wait_inputs.append(wait_input)
        self.queue_summary.setText(
            f"등록된 강의: {len(items)}개"
        )
        self.registration_count.setText(f"등록된 강의: {len(items)}개")
        self.latest_registration.setText(
            f"최근 등록: {items[-1][0]}" if items else "아직 등록된 강의가 없습니다."
        )
        self._update_playback_summary()
        self._update_step_controls()

    def _update_playback_summary(self) -> None:
        remaining = len(self._queue_entries)
        self.playback_progress.setText(
            f"완료 {max(0, self._playback_total - remaining)} / 전체 {self._playback_total}"
            f" · 남은 강의 {remaining}개"
        )
        self.playback_current.setText(
            f"현재 대상: {self._queue_entries[0][0]}" if remaining else "재생할 강의가 없습니다."
        )

    def set_workflow_state(self, state: str, has_queue: bool) -> None:
        """Update controls; the controller owns browser readiness and capture state."""
        registering = state in {"registering", "waiting_list", "pending_player"}
        playing = state == "playing"
        active = registering or playing
        ready = state in {"idle", "stopped"}
        previous_state = self._state
        self._state = state
        self._has_queue = has_queue
        self._playback_ready = False
        self.playback_readiness_status.setText("강의 목록을 확인하는 중입니다…")
        self._queue_editable = not playing
        for wait_input in self.queue_wait_inputs:
            wait_input.setEnabled(self._queue_editable)

        self.browser_button.setEnabled(not active)
        self.coordinate_test_button.setEnabled(not active)
        self.calibrate_button.setEnabled(not active)
        self.register_button.setEnabled(ready)
        self.stop_button.setEnabled(active)
        self.clear_queue_button.setEnabled(ready and has_queue)
        if playing:
            if previous_state != "playing":
                self._playback_total = len(self._queue_entries)
            self._update_playback_summary()
            self._show_step(4)
        elif state == "pending_player" and self._step == 3:
            self._show_step(2)
        elif previous_state == "playing" and state == "stopped":
            self._show_step(3 if self._browser_connected else 0)
        elif state == "stopped" and self._step >= 2 and not self._browser_connected:
            self._show_step(0)
        elif ready and not has_queue and self._step == 3:
            self._show_registration()
        self._update_step_controls()
