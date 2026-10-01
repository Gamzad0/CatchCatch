from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CatchCatch")
        self.resize(300, 100)

        self.start_button = QPushButton("시작")
        self.stop_button = QPushButton("중지")

        layout = QHBoxLayout(self)
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
