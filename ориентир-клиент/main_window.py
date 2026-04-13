import sys
import os
import json

from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QLabel, QLineEdit, QMenu, QVBoxLayout
from PySide6.QtGui import QFont, QPainter, QColor, QAction, QPen, Qt
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QRectF, Property, Signal, QPoint


class ToggleSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setFixedSize(200, 100)
        self._checked = False
        self._position = 0.0

        self.animation = QPropertyAnimation(self, b"position")
        self.animation.setDuration(350)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutQuad)

    @Property(float)
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        self._position = value
        self.update()

    @Property(bool)
    def isChecked(self):
        return self._checked

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(Qt.NoPen))

        width = self.width()
        height = self.height()
        radius = height // 2
        circle_size = height - 12
        max_x = width - circle_size - 6
        x = 6 + self._position * max_x
        y = 6

        if self._checked:
            bg_color = QColor(76, 175, 80)
        else:
            bg_color = QColor(200, 200, 200)

        painter.setBrush(bg_color)
        painter.drawRoundedRect(0, 0, width, height, radius, radius)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QRectF(x, y, circle_size, circle_size))

    def mousePressEvent(self, event):
        self._checked = not self._checked
        self.animation.stop()
        self.animation.setStartValue(self._position)
        self.animation.setEndValue(1.0 if self._checked else 0.0)
        self.animation.start()
        self.toggled.emit(self._checked)

class MainWindow(QWidget):
    traficZnachenie = 120  # временные переменные
    Format = "Мб"
    spedZnachenie = 2
    FormatSped = "Мб/C"
    podklZnach = 300
    VremesZnachenie = 60

    def __init__(self):
        super().__init__()
        self.ping = 30
        self.speed = 30
        self.speed1 = 30

        self.settings_file = "settings.json"
        self.server = ""
        self.port = 443
        self.password = ""

        self.setWindowTitle("VPN")
        self.setGeometry(100, 100, 220, 280)
        self.setFixedSize(300, 500)
        self.setStyleSheet("background-color: #26252d;")

        #Установка ползунка
        self.toggle_button = ToggleSwitch(self)
        self.toggle_button.setGeometry(50, 200, 200, 200)
        self.toggle_button.toggled.connect(self.on_button_on)

        #Установка кнопки ⋮
        self.button4 = QPushButton("⋮", self)
        self.button4.setGeometry(285, 0, 10, 30)
        self.button4.setFixedSize(15, 40)
        self.button4.setFont(QFont("Montserrat", 30))
        self.button4.setStyleSheet("""
        QPushButton {
            background-color: transparent;
            border: none;
            color:white 
        }
        
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.1);
        }
        """)
        self.button4.clicked.connect(self.show_menu)

        # Статус подключения
        self.status_label = QLabel("Отключено", self)
        self.status_label.setGeometry(90, 310, 200, 20)
        self.status_label.setFixedSize(225, 30)
        self.status_label.setStyleSheet("color: white; font-size: 20px; ")
        self.status_label.setFont(QFont("Montserrat", 30, QFont.Weight.Medium))

        # Пинг
        self.ping_label = QLabel("Ping: ", self)
        self.ping_label.setGeometry(115, 335, 200, 20)
        self.ping_label.setFixedSize(225, 35)
        self.ping_label.setFont(QFont("JetBrains Mono", 9, QFont.Weight.Medium))
        self.ping_label.setVisible(False)

        # Скорость
        self.speed_label = QLabel("Speed: ", self)
        self.speed_label.setGeometry(95, 365, 200, 20)
        self.speed_label.setFixedSize(150, 20)
        self.speed_label.setFont(QFont("JetBrains Mono", 9, QFont.Weight.Medium))
        self.speed_label.setVisible(False)

        self.load_settings()
        self.overlay = OverlayDialog(self)

    def load_settings(self):
        if os.path.exists(self.settings_file):
            with open(self.settings_file, "r", encoding='utf-8') as f:
                data = json.load(f)

            self.server = data["server"]
            self.port = data["port"]
            self.password = data["password"]
        else:
            data = {
                "server": "",
                "port": 443,
                "password": ""
            }
            with open("settings.json", "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)

    def save_settings(self):
        data_save = {
            "server": self.server,
            "port": self.port,
            "password": self.password
        }

        with open(self.settings_file, "w", encoding='utf-8') as f:
            json.dump(data_save, f, ensure_ascii=False, indent=4)

    def save_dialog_settings(self):
        new_server = self.server_edit.text()
        new_port_str = self.port_edit.text()
        new_password = self.password_edit.text()

        if new_port_str:
            new_port = int(new_port_str)
        else:
            new_port = 443

        self.server = new_server
        self.port = new_port
        self.password = new_password

        self.save_settings()

        self.overlay.hide_overlay()

    # Кнопка включения/подключения
    def on_button_on(self, is_checked):
        if is_checked:
            self.status_label.setText("Подключено")
            self.status_label.setGeometry(90, 310, 200, 20)
            self.status_label.setStyleSheet("color: #4CAF50; font-size: 20px;")

            self.ping_label.setText(f"Ping: {self.ping} ms")
            self.ping_label.setVisible(True)

            self.speed_label.setText(f"Speed: ↓ {self.speed} | ↑ {self.speed1}")
            self.speed_label.setVisible(True)

        else:
            self.status_label.setText("Отключено")
            self.status_label.setGeometry(90, 310, 200, 20)
            self.status_label.setStyleSheet("color: white; font-size: 20px;")

            self.ping_label.setText(f"Ping:")
            self.ping_label.setStyleSheet("color: white")
            self.ping_label.setVisible(False)

            self.speed_label.setText(f"Speed:")
            self.speed_label.setStyleSheet("color: white")
            self.speed_label.setVisible(False)

    def show_settings_overlay(self):
        content = QWidget()

        # Метка "Сервер"
        self.label = QLabel("Сервер:", content)
        self.label.setStyleSheet("color: white; font-size: 12px;")

        # Поле для ввода сервера
        self.server_edit = QLineEdit(content)
        self.server_edit.setFixedSize(220, 25)
        self.server_edit.setText(self.server)
        self.server_edit.setPlaceholderText("Введите адрес сервера")
        self.server_edit.setStyleSheet("background-color: white; color: black;")

        # Метка "Порт"
        self.label2 = QLabel("Порт:", content)
        self.label2.setStyleSheet("color: white; font-size: 12px;")

        # Поле для ввода порта
        self.port_edit = QLineEdit(content)
        self.port_edit.setFixedSize(220, 25)
        self.port_edit.setText(str(self.port))
        self.port_edit.setPlaceholderText("По умолчанию 443")
        self.port_edit.setStyleSheet("background-color: white; color: black;")

        # Метка "Пароль"
        self.label3 = QLabel("Пароль:", content)
        self.label3.setStyleSheet("color: white; font-size: 12px;")

        # Поле для ввода пароля
        self.password_edit = QLineEdit(content)
        self.password_edit.setFixedSize(220, 25)
        self.password_edit.setText(self.password)
        self.password_edit.setPlaceholderText("Введите пароль")
        self.password_edit.setStyleSheet("background-color: white; color: black;")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)

        self.save_button = QPushButton(content)
        self.save_button.setText("Сохранить")
        self.save_button.setStyleSheet("color: white")
        self.save_button.clicked.connect(self.save_dialog_settings)

        self.cancel_button = QPushButton(content)
        self.cancel_button.setText("Отмена")
        self.cancel_button.setStyleSheet("color: white")
        self.cancel_button.clicked.connect(self.overlay.hide_overlay)

        layout = QVBoxLayout(content)
        layout.addWidget(self.label)
        layout.addWidget(self.server_edit)
        layout.addWidget(self.label2)
        layout.addWidget(self.port_edit)
        layout.addWidget(self.label3)
        layout.addWidget(self.password_edit)
        layout.addWidget(self.save_button)
        layout.addWidget(self.cancel_button)

        self.overlay.show_with_content(content)

    # Настройки
    def on_button_settings(self):
        self.show_settings_overlay()

    def show_stats_overlay(self):
        content = QWidget()

        trafic_label = QLabel(f"Всего использовано: {self.traficZnachenie} {self.Format}", content)
        speed_label = QLabel(f"Средняя скорость: {self.spedZnachenie} {self.FormatSped}", content)
        connection_label = QLabel(f"Количество подключений за 24 часа: {self.podklZnach} раз", content)
        time_label = QLabel(f"Всего времени проведено: {self.VremesZnachenie} минут", content)

        exit_button = QPushButton("Выход",content)
        exit_button.clicked.connect(self.overlay.hide_overlay)

        layout = QVBoxLayout(content)
        layout.addWidget(trafic_label)
        layout.addWidget(speed_label)
        layout.addWidget(connection_label)
        layout.addWidget(time_label)
        layout.addWidget(exit_button)

        self.overlay.show_with_content(content)

    def show_menu(self):
        menu = QMenu(self)

        act1 = QAction("⚙️Настройки", self)
        act1.triggered.connect(self.show_settings_overlay)
        menu.addAction(act1)

        act2 = QAction("📊Статистика", self)
        act2.triggered.connect(self.show_stats_overlay)
        menu.addAction(act2)

        menu.setStyleSheet("""
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 0.1);
                color: white;
            }
        """)

        position = self.button4.mapToGlobal(QPoint(0,self.button4.height()))
        menu.exec(position)


class OverlayDialog(QWidget):
    def __init__(self, parent_main_window):
        super().__init__(parent_main_window)

        self.main_window = parent_main_window
        self._is_hiding = False

        self._position = 1.0

        self.setVisible(False)
        self.setStyleSheet("""background-color: #26252d;""")

        self.animation = QPropertyAnimation(self, b"position")
        self.animation.setDuration(300)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutQuad)

        self.container = QWidget(self)
        self.container.setStyleSheet("""
        color: white;
        background-color: #26252d;
        border-radius: 10px;
        padding: 20px;
        """)
        self.container.setFixedWidth(280)

        self.container_layout = QVBoxLayout()
        self.container.setLayout(self.container_layout)

        self.animation.finished.connect(self.on_hide_finished)

    @Property(float)
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        self._position = value
        self.updateContainerPosition()

    def updateContainerPosition(self):
        if not self.main_window:
            return

        overlay_width = self.width()
        overlay_height = self.height()
        container_width = self.container.width()
        container_height = self.container.height()

        center_x = (overlay_width - container_width) // 2
        y = (overlay_height - container_height) // 2

        hidden_x = overlay_width

        x = center_x + (hidden_x - center_x) * self._position

        self.container.move(x, y)

    def show_with_content(self, content_widget):
        self.animation.stop()
        self.clear_container()

        self.container_layout.addWidget(content_widget)

        self.container.updateGeometry()
        self.container_layout.activate()

        self._position = 1.0
        self.updateContainerPosition()
        self.setVisible(True)

        self.main_window.button4.setVisible(False)

        self.animation.setStartValue(1.0)
        self.animation.setEndValue(0.0)
        self.animation.start()

    def hide_overlay(self):
        self._is_hiding = True
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.start()

    def resizeEvent(self, event):
        self.setGeometry(self.main_window.rect())
        super().resizeEvent(event)
        self.updateContainerPosition()


    def on_hide_finished(self):
        if self._is_hiding:
            self.clear_container()
            self.setVisible(False)
            self.main_window.button4.setVisible(True)
            self._is_hiding = False
            self._position = 1.0
            self.updateContainerPosition()

    def clear_container(self):
        if not self.container_layout:
            return
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())