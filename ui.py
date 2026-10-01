from PySide6.QtWidgets import QLabel, QHBoxLayout, QPushButton, QVBoxLayout, QWidget


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CatchCatch")
        self.resize(440, 120)

        self.browser_button = QPushButton("브라우저 연결")
        self.start_button = QPushButton("시작")
        self.stop_button = QPushButton("중지")
        self.browser_status = QLabel("브라우저를 연결한 뒤 열린 Chrome에서 LMS에 로그인하세요.")
        self.browser_status.setWordWrap(True)

        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        buttons.addWidget(self.browser_button)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)
        layout.addWidget(self.browser_status)
