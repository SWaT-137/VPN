import sys
import os
import json

from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QLabel, QLineEdit, QMenu, QVBoxLayout
from PySide6.QtGui import QFont, QPainter, QColor, QAction, QPen, Qt
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QRectF, Property, Signal, QPoint, QTimer


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
        """)
        self.container.setFixedWidth(320)

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
        self.subscription_link = ""

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
        self.button4.setFont(QFont("Inter", 30))
        self.button4.setStyleSheet("""
        QPushButton {
            background-color: transparent;
            border: none;
            color:white 
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.08);
        }
        """)
        self.button4.clicked.connect(self.show_menu)

        # Статус подключения
        self.status_label = QLabel("Отключено", self)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setGeometry(37, 310, 225, 30)
        self.status_label.setStyleSheet("""
            color: #A0A0B0; 
            font-size: 22px; 
            font-family: 'Inter';
            font-weight: 500;
            background-color: transparent;
        """)

        # Пинг
        self.ping_label = QLabel("Ping: ", self)
        self.ping_label.setAlignment(Qt.AlignCenter)
        self.ping_label.setGeometry(37, 335, 225, 35)
        self.ping_label.setStyleSheet("color: #A0A0B0;")
        self.ping_label.setFont(QFont("JetBrains Mono", 12, QFont.Weight.Medium))
        self.ping_label.setVisible(False)

        # Скорость
        self.speed_label = QLabel("Speed: ", self)
        self.speed_label.setAlignment(Qt.AlignCenter)
        self.speed_label.setGeometry(37, 365, 225, 35)
        self.speed_label.setStyleSheet("color: #A0A0B0;")
        self.speed_label.setFont(QFont("JetBrains Mono", 12, QFont.Weight.Medium))
        self.speed_label.setVisible(False)

        #Таймер
        self.timer_seconds = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_timer)

        self.timer_label = QLabel("Время подключения: 00:00:00", self)
        self.timer_label.setAlignment(Qt.AlignCenter)
        self.timer_label.setGeometry(40, 395, 200, 20)
        self.timer_label.setFixedSize(235, 40)
        self.timer_label.setStyleSheet("color: #A0A0B0;")
        self.timer_label.setFont(QFont("JetBrains Mono", 12, QFont.Weight.Medium))
        self.timer_label.setVisible(False)

        self.load_settings()
        self.overlay = OverlayDialog(self)


    def update_timer(self):
        self.timer_seconds += 1
        hours = self.timer_seconds // 3600
        minutes = (self.timer_seconds % 3600) // 60
        seconds = self.timer_seconds % 60
        time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        self.timer_label.setText(f"Время подключения: {time_str}")


    # Кнопка включения/подключения
    def on_button_on(self, is_checked):
        if is_checked:
            self.status_label.setText("Подключено")
            self.status_label.setStyleSheet("""
                   color: #4CAF50; 
                   font-size: 22px; 
                   font-family: 'Inter';
                   font-weight: 600; 
                   background-color: transparent;
               """)

            self.ping_label.setText(f"Ping: {self.ping} ms")
            self.ping_label.setVisible(True)

            self.speed_label.setText(f"Speed: ↓ {self.speed} | ↑ {self.speed1}")
            self.speed_label.setVisible(True)

            self.timer_seconds = 0
            self.timer.start(1000)

            self.timer_label.setVisible(True)
            self.update_timer()

        else:
            self.status_label.setText("Отключено")
            self.status_label.setStyleSheet("""
                   color: #A0A0B0; 
                   font-size: 22px; 
                   font-family: 'Inter';
                   font-weight: 500;
                   background-color: transparent;
               """)

            self.ping_label.setVisible(False)

            self.speed_label.setVisible(False)

            self.timer_label.setVisible(False)
            self.timer.stop()


    def on_button_settings(self):
        self.show_settings_overlay()


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



    def show_menu(self):
        menu = QMenu(self)


        act1 = QAction("⚙️Настройки", self)
        act1.triggered.connect(self.show_settings_overlay)
        menu.addAction(act1)

        act2 = QAction("📊Статистика", self)
        act2.triggered.connect(self.show_stats_overlay)
        menu.addAction(act2)

        menu.setStyleSheet("""
                   QMenu {
                       background-color: #2e2d38;
                       border: 1px solid #3a3944;
                       border-radius: 10px;
                       padding: 5px;
                       color: #A0A0B0;
                       font-family: 'Inter';
                       font-size: 13px;
                   }
                   QMenu::item {
                       padding: 8px 25px;
                       border-radius: 6px;
                       margin: 2px 5px;
                   }
                   QMenu::item:selected {
                       background-color: rgba(255, 255, 255, 0.08);
                       color: white;
                   }
               """)

        position = self.button4.mapToGlobal(QPoint(0,self.button4.height()))
        menu.exec(position)


    def show_settings_overlay(self):
        content = QWidget()

        content.setLayout(None)
        content.setFixedSize(320, 500)

        # Метка "Ссылка для подписки"
        self.sub_label = QLabel("Ссылка для подписки:", content)
        self.sub_label.setGeometry(10, 180, 280, 30)
        self.sub_label.setStyleSheet("""
            color: white;
            font-size: 20px; 
            font-weight: bold;
        """)

        # Поле для ввода ссылки
        self.sub_link_edit = QLineEdit(content)
        self.sub_link_edit.setFixedSize(280, 50)
        self.sub_link_edit.setGeometry(10, 215, 280, 35)
        self.sub_link_edit.setText(self.subscription_link)
        self.sub_link_edit.setPlaceholderText("Вставьте ссылку")
        self.sub_link_edit.setStyleSheet("""
            background-color: #404040;
            color: #A0A0B0;
            border-radius: 5px;
            border: 1px solid #555555;
            padding: 2px 5px;
        """)

        self.save_button = QPushButton("Сохранить", content)
        self.save_button.setCursor(Qt.PointingHandCursor)

        self.save_button.setGeometry(100, 290, 120, 45)
        self.save_button.setFixedSize(120, 45)
        self.save_button.setStyleSheet("""
            background-color: #4CAF50;
            color: white;
            border: none;
            border-radius: 5px;
            padding: 5px;
            font-weight: bold;
            font-family: 'Inter';
        """)
        self.save_button.clicked.connect(self.save_dialog_settings)

        self.cancel_button = QPushButton("←", content)
        self.cancel_button.setCursor(Qt.PointingHandCursor)

        self.cancel_button.setGeometry(10, 10, 35, 35)
        self.cancel_button.setFixedSize(35, 35)

        self.cancel_button.setStyleSheet("""
        QPushButton {
            background-color: transparent;
            color: white;
            border-radius: 17px;
            padding: 0px;
            padding-top: -3px;
            font-size: 22px;
            font-family: 'Inter'

        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.05);
        }
        """)
        self.cancel_button.clicked.connect(self.overlay.hide_overlay)

        self.overlay.show_with_content(content)

    def show_stats_overlay(self):
        content = QWidget()

        content.setLayout(None)
        content.setFixedSize(300, 500)

        self.stats_trafic_label = QLabel(f"Всего использовано: ... {self.Format}", content)
        self.stats_trafic_label.setAlignment(Qt.AlignCenter)
        self.stats_trafic_label.setGeometry(2, 115, 20, 10)
        self.stats_trafic_label.setFixedSize(300, 70)
        self.stats_trafic_label.setStyleSheet("""
            color: #888888;
            font-size: 14px;
            margin: 5px;
            padding: 10px;
            background-color: #3a3944;
            border-radius: 8px;
        """)

        self.stats_speed_label = QLabel(f"Средняя скорость: ... {self.FormatSped}", content)
        self.stats_speed_label.setAlignment(Qt.AlignCenter)
        self.stats_speed_label.setGeometry(2, 195, 20, 10)
        self.stats_speed_label.setFixedSize(300, 70)
        self.stats_speed_label.setStyleSheet("""
            color: #888888;
            font-size: 14px;
            margin: 5px;
            padding: 10px;
            background-color: #3a3944;
            border-radius: 8px;
        """)

        self.stats_connection_label = QLabel(f"Количество подключений за 24 часа: ... \nраз", content)
        self.stats_connection_label.setAlignment(Qt.AlignCenter)
        self.stats_connection_label.setGeometry(2, 275, 20, 10)
        self.stats_connection_label.setFixedSize(300, 70)
        self.stats_connection_label.setStyleSheet("""
            color: #888888;
            font-size: 14px;
            margin: 5px;
            padding: 10px;
            background-color: #3a3944;
            border-radius: 8px;
        """)

        self.exit_button = QPushButton("←", content)
        self.exit_button.setCursor(Qt.PointingHandCursor)

        self.exit_button.setGeometry(10, 10, 35, 35)
        self.exit_button.setFixedSize(35, 35)

        self.exit_button.setStyleSheet("""
        QPushButton {
            background-color: transparent;
            color: white;
            border-radius: 17px;
            padding: 0px;
            padding-top: -3px;
            font-size: 22px;
            font-family: 'Inter'

        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 0.05);
        }
        """)
        self.exit_button.clicked.connect(self.overlay.hide_overlay)

        self.overlay.show_with_content(content)

        QTimer.singleShot(1500, self.load_stats)



    def load_settings(self):
        if os.path.exists(self.settings_file):
            with open(self.settings_file, "r", encoding='utf-8') as f:
                data = json.load(f)

            self.subscription_link = data.get("subscription_link", "")
        else:
            self.save_settings()


    def save_settings(self):
        data_save = {
            "subscription_link": self.subscription_link
        }

        with open(self.settings_file, "w", encoding='utf-8') as f:
            json.dump(data_save, f, ensure_ascii=False, indent=4)


    def load_stats(self):
        if hasattr(self, 'stats_trafic_label') and self.stats_trafic_label:
            self.stats_trafic_label.setText(f"Всего использовано: {self.traficZnachenie} {self.Format}")
            self.stats_trafic_label.setStyleSheet(self.stats_trafic_label.styleSheet().replace("color: #888888;", "color: white;"))

        if hasattr(self, 'stats_speed_label') and self.stats_speed_label:
            self.stats_speed_label.setText(f"Средняя скорость: {self.spedZnachenie} {self.FormatSped}")
            self.stats_speed_label.setStyleSheet(self.stats_speed_label.styleSheet().replace("color: #888888;", "color: white;"))

        if hasattr(self, 'stats_connection_label') and self.stats_connection_label:
            self.stats_connection_label.setText(f"Количество подключений за 24 часа: {self.podklZnach}\nраз")
            self.stats_connection_label.setStyleSheet(self.stats_connection_label.styleSheet().replace("color: #888888;", "color: white;"))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())