from PySide6.QtWidgets import QLabel, QHBoxLayout, QPushButton, QVBoxLayout, QWidget


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CatchCatch")
        self.resize(520, 190)

        self.browser_button = QPushButton("브라우저 연결")
        self.coordinate_test_button = QPushButton("좌표 변환 테스트")
        self.start_button = QPushButton("시작")
        self.stop_button = QPushButton("중지")
        self.browser_status = QLabel("브라우저를 연결하면 보정 화면이 열립니다.")
        self.browser_status.setWordWrap(True)
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
        layout.addWidget(self.calibrate_button)
        layout.addWidget(self.cursor_status)
