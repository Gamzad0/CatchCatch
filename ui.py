from collections.abc import Iterable

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CatchCatch")
        self.resize(640, 600)

        self.browser_button = QPushButton("브라우저 연결")
        self.coordinate_test_button = QPushButton("좌표 변환 테스트")
        self.register_button = QPushButton("영상 등록 시작")
        self.finish_registration_button = QPushButton("등록 완료 · 자동 재생")
        self.start_button = QPushButton("자동 재생")
        self.stop_button = QPushButton("중지")
        self.clear_queue_button = QPushButton("등록 목록 비우기")
        self.browser_status = QLabel("브라우저를 연결하면 보정 화면이 열립니다.")
        self.browser_status.setWordWrap(True)
        self.calibration_instructions = QLabel(
            "보정 방법: 브라우저 연결을 누르면 Chrome은 화면 왼쪽, 앱은 오른쪽에 배치됩니다. "
            "창 배치가 완료되면 보정이 자동으로 시작됩니다. "
            "Chrome 첫 페이지의 빈 곳을 클릭한 뒤, 5초 안에 '보정 기준' 버튼의 "
            "빨간 + 중앙에 커서를 놓고 유지하세요. 보정 완료 후 같은 탭에서 LMS로 이동하세요. "
            "시간을 놓쳤다면 '다시 보정'을 누르세요.")
        self.calibration_instructions.setWordWrap(True)
        self.calibrate_button = QPushButton("다시 보정")
        self.cursor_status = QLabel("커서 아래 버튼: 보정이 필요합니다.")
        self.cursor_status.setWordWrap(True)
        self.registration_instructions = QLabel(
            "영상 등록: LMS 강의 목록에서 시청할 항목을 클릭하세요. "
            "같은 탭에서 재생 페이지와 영상 정보가 준비되면 자동으로 등록합니다. "
            "등록 완료 안내를 확인하세요. 영상 길이에 3분을 더해 순서대로 대기합니다. "
            "직접 강의 목록으로 돌아가 다음 영상을 같은 방법으로 등록하세요. "
            "모든 영상을 등록한 뒤 강의 목록으로 돌아와 '등록 완료 · 자동 재생'을 누르세요.\n"
            "등록 정보는 앱 실행 중 메모리에 보관합니다. "
            "등록과 자동 재생 중에는 보정한 같은 탭을 사용하고 브라우저 창의 위치·크기와 확대 비율을 유지하세요. "
            "자동 재생은 현재 플레이어 중앙을 클릭합니다."
        )
        self.registration_instructions.setWordWrap(True)
        self.registration_status = QLabel("영상 등록 대기")
        self.registration_status.setWordWrap(True)
        self.playback_status = QLabel("자동 재생 대기")
        self.playback_status.setWordWrap(True)
        self.queue_summary = QLabel("등록된 영상: 0개 · 등록 순서대로 재생")
        self.queue_list = QListWidget()

        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        buttons.addWidget(self.browser_button)
        buttons.addWidget(self.coordinate_test_button)
        layout.addLayout(buttons)
        layout.addWidget(self.browser_status)
        layout.addWidget(self.calibration_instructions)
        layout.addWidget(self.calibrate_button)
        layout.addWidget(self.cursor_status)
        layout.addWidget(self.registration_instructions)
        registration_buttons = QHBoxLayout()
        registration_buttons.addWidget(self.register_button)
        registration_buttons.addWidget(self.finish_registration_button)
        registration_buttons.addWidget(self.start_button)
        registration_buttons.addWidget(self.stop_button)
        layout.addLayout(registration_buttons)
        layout.addWidget(self.registration_status)
        queue_header = QHBoxLayout()
        queue_header.addWidget(self.queue_summary)
        queue_header.addWidget(self.clear_queue_button)
        layout.addLayout(queue_header)
        layout.addWidget(self.queue_list)
        layout.addWidget(self.playback_status)
        self.set_workflow_state("idle", has_queue=False)

    def set_queue(self, entries: Iterable[str]) -> None:
        """Show caller-formatted labels, durations, wait times and queue states."""
        items = list(entries)
        self.queue_list.clear()
        self.queue_list.addItems(items)
        self.queue_summary.setText(
            f"등록된 영상: {len(items)}개 · 등록 순서대로 재생"
        )

    def set_workflow_state(self, state: str, has_queue: bool) -> None:
        """Update controls; the controller owns browser readiness and capture state."""
        registering = state in {"registering", "waiting_list", "pending_player"}
        playing = state == "playing"
        active = registering or playing
        ready = state in {"idle", "stopped"}

        self.browser_button.setEnabled(not active)
        self.coordinate_test_button.setEnabled(not active)
        self.calibrate_button.setEnabled(not active)
        self.register_button.setEnabled(ready)
        self.finish_registration_button.setEnabled(
            registering and state != "pending_player" and has_queue
        )
        self.start_button.setEnabled(ready and has_queue)
        self.stop_button.setEnabled(active)
        self.clear_queue_button.setEnabled(ready and has_queue)
