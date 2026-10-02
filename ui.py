from PySide6.QtWidgets import QLabel, QHBoxLayout, QPushButton, QVBoxLayout, QWidget


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CatchCatch")
        self.resize(520, 240)

        self.browser_button = QPushButton("브라우저 연결")
        self.coordinate_test_button = QPushButton("좌표 변환 테스트")
        self.start_button = QPushButton("시작")
        self.stop_button = QPushButton("중지")
        self.browser_status = QLabel("브라우저를 연결하면 보정 화면이 열립니다.")
        self.browser_status.setWordWrap(True)
        self.calibration_instructions = QLabel(
            "보정 방법: 브라우저 연결을 누르면 보정이 자동으로 시작됩니다. "
            "Chrome 첫 페이지의 빈 곳을 클릭한 뒤, 5초 안에 '보정 기준' 버튼의 "
            "빨간 + 중앙에 커서를 놓고 유지하세요. 보정 완료 후 같은 탭에서 LMS로 이동하세요. "
            "시간을 놓쳤다면 '다시 보정'을 누르세요.")
        self.calibration_instructions.setWordWrap(True)
        self.calibrate_button = QPushButton("다시 보정")
        self.cursor_status = QLabel("커서 아래 버튼: 보정이 필요합니다.")
        self.cursor_status.setWordWrap(True)

        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        buttons.addWidget(self.browser_button)
        buttons.addWidget(self.coordinate_test_button)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)
        layout.addWidget(self.browser_status)
        layout.addWidget(self.calibration_instructions)
        layout.addWidget(self.calibrate_button)
        layout.addWidget(self.cursor_status)
