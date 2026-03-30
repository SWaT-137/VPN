import sys
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QDialog, QLabel, QLineEdit, QMessageBox, QCheckBox, QMenu
from PySide6.QtGui import QFont, QPainter, QColor, QAction, QPen, Qt
from PySide6.QtCore import QSettings, QPropertyAnimation, QEasingCurve, QRectF, Property, Signal, QPoint


class ToggleSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setFixedSize(200, 100)
        self._checked = False
        self._position = 0.0

        self.animation = QPropertyAnimation(self, b"position")
        self.animation.setDuration(300)
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
        circle_size = height - 8
        max_x = width - circle_size - 4
        x = 4 + self._position * max_x
        y = 4

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

        self.setWindowTitle("VPN")
        self.setGeometry(100, 100, 220, 280)
        self.setFixedSize(300, 500)
        self.setStyleSheet("background-color: #26252d;")
        self.settings = QSettings("MyVPN", "VPNSettings")

        #Установка ползунка
        self.toggle_button = ToggleSwitch(self)
        self.toggle_button.setGeometry(50, 200, 200, 200)
        self.toggle_button.toggled.connect(self.on_button_on)

        #Установка кнопки ⋮
        self.button4 = QPushButton("⋮", self)
        self.button4.setGeometry(285, 0, 10, 30)
        self.button4.setFixedSize(15, 40)
        self.button4.setFont(QFont("Segoe UI", 30))
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
        self.status_label.setGeometry(100, 315, 200, 20)
        self.status_label.setFixedSize(225, 30)
        self.status_label.setStyleSheet("color: white; font-size: 20px;")

        # Пинг
        self.ping_label = QLabel("Ping: ", self)
        self.ping_label.setGeometry(65, 350, 200, 20)
        self.ping_label.setFixedSize(225, 35)
        self.ping_label.setVisible(False)

        # Скорость
        self.speed_label = QLabel("Speed: ", self)
        self.speed_label.setGeometry(150, 350, 200, 20)
        self.speed_label.setFixedSize(100, 35)
        self.speed_label.setVisible(False)


    # Кнопка включения/подключения
    def on_button_on(self, is_checked):
        if is_checked:
            self.status_label.setText("Подключено")
            self.status_label.setGeometry(90, 315, 200, 20)
            self.status_label.setStyleSheet("color: #4CAF50; font-size: 20px;")
            self.ping_label.setText(f"Ping: {self.ping} ms")
            self.ping_label.setVisible(True)
            self.speed_label.setText(f"Speed: ↓ {self.speed} | ↑ {self.speed1}")
            self.speed_label.setVisible(True)

        else:
            self.status_label.setText("Отключено")
            self.status_label.setGeometry(100, 315, 200, 20)
            self.status_label.setStyleSheet("color: white; font-size: 20px;")
            self.ping_label.setText(f"Ping:")
            self.ping_label.setVisible(False)
            self.speed_label.setText(f"Speed:")
            self.speed_label.setVisible(False)

    # Настройки
    def on_button_settings(self):
        print("Кнопка 'Настройка' нажата")
        self.open_new_window()

    # Открытие нового окна
    def open_new_window(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Настройки")
        dialog.setGeometry(200, 200, 300, 300)
        dialog.setFixedSize(240, 300)
        dialog.setStyleSheet("background-color: #26252d;")

        # Метка "Сервер"
        label = QLabel("Сервер:", dialog)
        label.setGeometry(10, 10, 100, 20)
        label.setStyleSheet("color: white; font-size: 12px;")

        # Поле для ввода сервера
        self.server_edit = QLineEdit(dialog)
        self.server_edit.setGeometry(10, 30, 220, 25)
        self.server_edit.setPlaceholderText("Введите адрес сервера")
        self.server_edit.setStyleSheet("background-color: white; color: black;")

        # Метка "Порт"
        label2 = QLabel("Порт:", dialog)
        label2.setGeometry(10, 60, 100, 20)
        label2.setStyleSheet("color: white; font-size: 12px;")

        # Метка "По умолчанию"
        label4 = QLabel("По умолчанию 443", dialog)
        label4.setGeometry(100, 60, 110, 20)
        label4.setStyleSheet("color: white; font-size: 12px;")

        # Поле для ввода порта
        self.port_edit = QLineEdit(dialog)
        self.port_edit.setGeometry(10, 80, 220, 25)
        self.port_edit.setPlaceholderText("По умолчанию 443")
        self.port_edit.setStyleSheet("background-color: white; color: black;")


        # Метка "Пароль"
        label3 = QLabel("Пароль:", dialog)
        label3.setGeometry(10, 110, 100, 20)
        label3.setStyleSheet("color: white; font-size: 12px;")

        # Поле для ввода пароля
        self.password_edit = QLineEdit(dialog)
        self.password_edit.setGeometry(10, 130, 220, 25)
        self.password_edit.setPlaceholderText("Введите пароль")
        self.password_edit.setStyleSheet("background-color: white; color: black;")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)

        # Кнопки
        self.checkbox = QCheckBox("Включить кнопку", dialog)
        self.checkbox.setGeometry(10, 165, 20, 20)
        self.checkbox.stateChanged.connect(self.on_checkbox_changed)

        # Кнопка сохранения
        self.save_btn = QPushButton("Сохранить", dialog)
        self.save_btn.setGeometry(10, 273, 100, 25)
        self.save_btn.clicked.connect(lambda: self.save_settings(dialog))

        # Кнопка отмены
        cancel_btn = QPushButton("Отмена", dialog)
        cancel_btn.setGeometry(130, 273, 100, 25)
        cancel_btn.clicked.connect(dialog.reject)

        dialog.exec()

    def on_checkbox_changed(self, state):
        self.button.setEnabled(state == 2)

    def save_settings(self, dialog):
        """Сохраняет настройки и закрывает окно"""
        # Получаем данные из полей ввода

        server = self.server_edit.text()
        port = self.port_edit.text()
        password = self.password_edit.text()

        # Проверяем, что поля не пустые
        if not server or not port or not password:
            QMessageBox.warning(dialog, "Предупреждение", "Заполните все поля!")
            return

        # Сохраняем настройки
        self.server = server
        self.port = port
        self.password = password

        # Сохраняем в QSettings
        self.settings.setValue("server", server)
        self.settings.setValue("port", port)
        self.settings.setValue("password", password)
        self.settings.sync()
        # Сэйв чекбокса
        self.settings.setValue("checkbox_state", self.checkbox.isChecked())

        print(f"Настройки сохранены: Сервер={server}, Порт={port}")

        QMessageBox.information(dialog, "Успех", "Настройки сохранены!")
        # Закрываем диалог
        dialog.accept()

    def open_new_window_stat(self):
        dialog = QDialog(self)  # Создаем окно
        dialog.setWindowTitle("Статистика")
        dialog.setGeometry(100, 100, 300, 250)
        dialog.setFixedSize(275, 250)
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

