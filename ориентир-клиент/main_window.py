import sys
import os
import json
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QDialog, QLabel, QLineEdit, QMenu
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

        self.dialog.accept()

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

    # Настройки
    def on_button_settings(self):
        print("Кнопка 'Настройка' нажата")
        self.open_new_window()

    # Открытие нового окна
    def open_new_window(self):
        self.dialog = QDialog(self)
        self.dialog.setWindowTitle("Настройки")
        self.dialog.setGeometry(200, 200, 300, 300)
        self.dialog.setFixedSize(240, 215)
        self.dialog.setStyleSheet("background-color: #26252d; color: white")

        # Метка "Сервер"
        label = QLabel("Сервер:", self.dialog)
        label.setGeometry(10, 10, 100, 20)
        label.setStyleSheet("color: white; font-size: 12px;")

        # Поле для ввода сервера
        self.server_edit = QLineEdit(self.dialog)
        self.server_edit.setGeometry(10, 30, 220, 25)
        self.server_edit.setFixedSize(220, 25)
        self.server_edit.setText(self.server)
        self.server_edit.setPlaceholderText("Введите адрес сервера")
        self.server_edit.setStyleSheet("background-color: white; color: black;")

        # Метка "Порт"
        label2 = QLabel("Порт:", self.dialog)
        label2.setGeometry(10, 60, 100, 20)
        label2.setStyleSheet("color: white; font-size: 12px;")

        # Метка "По умолчанию"
        label4 = QLabel("По умолчанию 443", self.dialog)
        label4.setGeometry(100, 60, 110, 20)
        label4.setStyleSheet("color: white; font-size: 12px;")

        # Поле для ввода порта
        self.port_edit = QLineEdit(self.dialog)
        self.port_edit.setGeometry(10, 80, 220, 25)
        self.port_edit.setFixedSize(220, 25)
        self.port_edit.setText(str(self.port))
        self.port_edit.setPlaceholderText("По умолчанию 443")
        self.port_edit.setStyleSheet("background-color: white; color: black;")

        # Метка "Пароль"
        label3 = QLabel("Пароль:", self.dialog)
        label3.setGeometry(10, 110, 100, 20)
        label3.setStyleSheet("color: white; font-size: 12px;")

        # Поле для ввода пароля
        self.password_edit = QLineEdit(self.dialog)
        self.password_edit.setGeometry(10, 130, 220, 25)
        self.password_edit.setFixedSize(220, 25)
        self.password_edit.setText(self.password)
        self.password_edit.setPlaceholderText("Введите пароль")
        self.password_edit.setStyleSheet("background-color: white; color: black;")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)

        self.save_button = QPushButton(self.dialog)
        self.save_button.setText("Сохранить")
        self.save_button.setGeometry(10, 170, 105, 40)
        self.save_button.setFixedSize(105, 40)
        self.save_button.setStyleSheet("color: white")
        self.save_button.clicked.connect(self.save_dialog_settings)

        self.cancel_button = QPushButton(self.dialog)
        self.cancel_button.setText("Отмена")
        self.cancel_button.setGeometry(125, 170, 105, 40)
        self.cancel_button.setFixedSize(105, 40)
        self.cancel_button.setStyleSheet("color: white")
        self.cancel_button.clicked.connect(self.dialog.close)

        self.dialog.exec()

    def open_new_window_stat(self):
        dialog = QDialog(self)  # Создаем окно
        dialog.setWindowTitle("Статистика")
        dialog.setGeometry(100, 100, 300, 250)
        dialog.setFixedSize(275, 250)
        dialog.setStyleSheet("color: white")
        dialog.setStyleSheet("background-color: #26252d;")

        label = QLabel("Всего использовано мб трафика:", dialog)  # Создаем лейбл с текстом
        label.setGeometry(10, 10, 200, 10)
        label.setStyleSheet("color: white; font-size: 12px;")
        trafic = QLabel(f"{self.traficZnachenie} {self.Format}", dialog)  # Создаем лейбл со значением трафика
        trafic.setGeometry(210, 10, 200, 10)

        label2 = QLabel("Средняя скорость:", dialog)  # Создаем лейбл с текстом
        label2.setGeometry(10, 60, 200, 20)
        label2.setStyleSheet("color: white; font-size: 12px;")
        sped = QLabel(f"{self.spedZnachenie} {self.FormatSped}", dialog)  # Создаем лейбл со значением скорости
        sped.setGeometry(120, 65, 200, 10)

        label3 = QLabel("Количество подключений за 24ч:", dialog)  # Создаем лейбл с текстом
        label3.setGeometry(10, 110, 200, 30)
        label3.setStyleSheet("color: white; font-size: 12px;")
        podkl = QLabel(f"{self.podklZnach} раз", dialog)  # Создаем лейбл со значением кол-во подключений
        podkl.setGeometry(210, 115, 200, 10)
        podkl.setFixedSize(50, 20)

        label4 = QLabel("Всего времени проведено: ", dialog)  # Создаем лейбл с текстом
        label4.setGeometry(10, 160, 200, 40)
        label4.setStyleSheet("color: white; font-size: 12px;")
        vremes = QLabel(f"{self.VremesZnachenie} минут", dialog)  # Создаем лейбл со значением кол-во подключений
        vremes.setGeometry(180, 175, 200, 10)

        cancel_btn = QPushButton("Выход", dialog)  # Кнопка выхода
        cancel_btn.setGeometry(100, 200, 100, 25)
        cancel_btn.setStyleSheet("color: white")
        cancel_btn.clicked.connect(dialog.reject)

        dialog.exec()

    def show_menu(self):
        menu = QMenu(self)

        act1 = QAction("⚙️Настройки", self)
        act1.triggered.connect(self.open_new_window)
        menu.addAction(act1)

        act2 = QAction("📊Статистика", self)
        act2.triggered.connect(self.open_new_window_stat)
        menu.addAction(act2)

        menu.setStyleSheet("""
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 0.1);
                color: white;
            }
        """)

        position = self.button4.mapToGlobal(QPoint(0,self.button4.height()))
        menu.exec(position)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())