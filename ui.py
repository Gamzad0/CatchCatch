from PySide6.QtWidgets import QLabel, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CatchCatch")
        self.resize(520, 190)

        self.browser_button = QPushButton("브라우저 연결")
        self.coordinate_test_button = QPushButton("좌표 변환 테스트")
        self.start_button = QPushButton("시작")
        self.stop_button = QPushButton("중지")
        self.browser_status = QLabel("브라우저를 연결한 뒤 열린 Chrome에서 LMS에 로그인하세요.")
        self.browser_status.setWordWrap(True)
        self.reference_selector = QLineEdit("#reference")
        self.reference_selector.setPlaceholderText("보정 기준 버튼의 CSS 선택자")
        self.calibrate_button = QPushButton("커서 위치 보정 (3초)")
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
        calibration = QHBoxLayout()
        calibration.addWidget(self.reference_selector)
        calibration.addWidget(self.calibrate_button)
        layout.addLayout(calibration)
        layout.addWidget(self.cursor_status)
