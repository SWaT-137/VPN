import sys
from PySide6 import QtWidgets
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QLabel, QDialog, QLineEdit, QMessageBox
from PySide6.QtCore import QSize, QTimer, QSettings
from PySide6.QtGui import QFont

ИмяСервера = ""
СтатусДанные = "Подключенно"
ПингДанные = 50
СкоростьДанные = 3.2
ТрафикДанные = 100
Пароль = ""
Порт = 443


class ОсновноеОкно(QMainWindow):
    ТаймерОбновления = QTimer()

    def Таймер(self):
        self.серверЗначение.setText(СтатусДанные)
        self.СтатусЗначение.setText(ИмяСервера)
        self.СтатусЗначение.setStyleSheet("""
            color: white;
            border-radius: 100px;
        """)
        self.ПингЗначение.setText(f"{ПингДанные} мс")
        self.СкоростьЗначение.setText(f"{СкоростьДанные} мб/c")
        self.ТрафикЗначение.setText(f"{ТрафикДанные} мб")

    def on_button_click(self):
        if self.подключение.text() == "Отключено":
            self.подключение.setText("Подключено")
            self.подключение.setStyleSheet("""
            background-color: green;
            color: white;
            border-radius: 100px;
        """)
            self.Пинг.setVisible(True)
            self.ПингЗначение.setVisible(True)
            self.Скорость.setVisible(True)
            self.СкоростьЗначение.setVisible(True)
            self.Трафик.setVisible(True)
            self.ТрафикЗначение.setVisible(True)
            self.ТаймерОбновления.start(1000)
            self.ТаймерОбновления.timeout.connect(self.Таймер)
        else:
            self.подключение.setText("Отключено")
            self.подключение.setStyleSheet("""
            background-color: #800000;
            color: white;
            border-radius: 100px;
        """)
            self.Пинг.setVisible(False)
            self.ПингЗначение.setVisible(False)
            self.Скорость.setVisible(False)
            self.СкоростьЗначение.setVisible(False)
            self.Трафик.setVisible(False)
            self.ТрафикЗначение.setVisible(False)
            self.серверЗначение.setText("Отключенно")
            self.ТаймерОбновления.stop()

    def привтун(self):
        self.button.setStyleSheet("background-color: green;")

    def открыть_новое_окно(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Настройки")
        dialog.setGeometry(200, 200, 250, 250)
        dialog.setStyleSheet("background-color: #26252d;")


        label = QLabel("Сервер:", dialog)
        label.setGeometry(10, 10, 100, 20)
        label.setStyleSheet("color: white; font-size: 12px;")

        self.серверИзменение = QLineEdit(dialog)
        self.серверИзменение.setGeometry(10, 30, 220, 25)
        self.серверИзменение.setPlaceholderText("Введите адрес для подключения")
        self.серверИзменение.setStyleSheet("background-color: white; color: black;")
        self.серверИзменение.setText(ИмяСервера)

        label2 = QLabel("Порт (по умолчанию 443):", dialog)
        label2.setGeometry(10, 70, 150, 20)
        label2.setStyleSheet("color: white; font-size: 12px;")

        self.портСервера = QLineEdit(dialog)
        self.портСервера.setGeometry(10, 90, 220, 25)
        self.портСервера.setPlaceholderText("Введите адрес для подключения")
        self.портСервера.setStyleSheet("background-color: white; color: black;")
        self.портСервера.setText(str(Порт))

        label3 = QLabel("Пароль для подключения:", dialog)
        label3.setGeometry(10, 130, 150, 20)
        label3.setStyleSheet("color: white; font-size: 12px;")

        self.парольИзменение = QLineEdit(dialog)
        self.парольИзменение.setGeometry(10, 150, 220, 25)
        self.парольИзменение.setPlaceholderText("Введите пароль для подключения")
        self.парольИзменение.setStyleSheet("background-color: white; color: black;")
        self.парольИзменение.setText(str(Пароль))

        self.save_btn = QPushButton("Сохранить", dialog)
        self.save_btn.setGeometry(10, 190, 100, 25)
        self.save_btn.clicked.connect(lambda: self.save_settings(dialog))


        cancel_btn = QPushButton("Отмена", dialog)
        cancel_btn.setGeometry(130, 190, 100, 25)
        cancel_btn.clicked.connect(dialog.reject)

        dialog.exec()


        dialog.exec()

    def load_settings(self):
        """Загружает настройки из файла"""
        self.settings = QSettings("MyVPN", "VPNSettings")
        self.server = self.settings.value("server", "")
        self.port = self.settings.value("port", "")
        self.password = self.settings.value("password", "")
        print(f"Загружены настройки: сервер={self.server}, порт={self.port}")


    def save_settings(self, dialog):

        server = self.серверИзменение.text()
        port = self.портСервера.text()
        password = self.парольИзменение.text()

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
        self.settings.sync()  # Принудительно сохраняем

        print(f"Настройки сохранены: Сервер={server}, Порт={port}")

        # Показываем сообщение об успешном сохранении
        QMessageBox.information(dialog, "Успех", "Настройки сохранены!")

        # Закрываем диалог
        dialog.accept()

    def __init__(self):
        super().__init__()
        self.setFixedSize(QSize(400, 700))  # Настраиваем СЕБЯ
        self.setWindowTitle("гойда впн")
        self.setStyleSheet("background-color: #26252d ")
        self.подключение = QPushButton("Отключено", self)
        self.подключение.setGeometry(100, 450, 200, 200)
        self.подключение.setToolTip("Нажмите для подключения")
        self.подключение.clicked.connect(self.on_button_click)
        self.подключение.setFont(QFont("Georgia", 19))
        self.подключение.setStyleSheet("""
            background-color: #800000;
            color: white;
            border-radius: 100px;
        """)


        self.настройки = QPushButton("Настройки", self)
        self.настройки.setGeometry(0, 600, 100, 100)
        self.настройки.setToolTip("Нажмите для открытия настроек")
        self.настройки.clicked.connect(self.открыть_новое_окно)
        self.настройки.setFont(QFont("Georgia", 10))
        self.настройки.setStyleSheet("""
                    background-color:  #201f25;
                    color: white;
                    border-radius: 25px;
                """)


        self.статистика = QPushButton("Статистика", self)
        self.статистика.setGeometry(300, 600, 100, 100)
        self.статистика.setToolTip("Нажмите для открытия статистики")
        self.статистика.clicked.connect(self.привтун)
        self.статистика.setFont(QFont("Georgia", 10))
        self.статистика.setStyleSheet("""
                            background-color:  #201f25;
                            color: white;
                            border-radius: 25px;
                        """)



        заголовок = QLabel("Добро пожаловать в гойда ВПН", self)
        заголовок.setGeometry(45, 1, 700, 100)
        заголовок.setFont(QFont("Georgia ", 16))

        self.сервер = QLabel("Статус: ", self)
        self.сервер.setGeometry(45, 100, 150, 25)
        self.сервер.setFont(QFont("Georgia ", 15))
        self.серверЗначение = QLabel("Не подключенно", self)
        self.серверЗначение.setGeometry(125, 100, 180, 25)
        self.серверЗначение.setFont(QFont("Georgia ", 15))

        self.Статус = QLabel("Сервер: ", self)
        self.Статус.setGeometry(45, 125, 150, 25)
        self.Статус.setFont(QFont("Georgia ", 15))
        self.СтатусЗначение = QLabel("", self)
        self.СтатусЗначение.setGeometry(125, 125, 150, 25)
        self.СтатусЗначение.setFont(QFont("Georgia ", 15))

        self.Пинг = QLabel("Пинг: ", self)
        self.Пинг.setGeometry(45, 175, 150, 25)
        self.Пинг.setFont(QFont("Georgia ", 15))
        self.Пинг.setVisible(False)
        self.ПингЗначение = QLabel("", self)
        self.ПингЗначение.setGeometry(110, 175, 150, 25)
        self.ПингЗначение.setFont(QFont("Georgia ", 15))
        self.ПингЗначение.setVisible(False)

        self.Скорость = QLabel("Скорость: ", self)
        self.Скорость.setGeometry(45, 200, 150, 25)
        self.Скорость.setFont(QFont("Georgia ", 15))
        self.Скорость.setVisible(False)
        self.СкоростьЗначение = QLabel("", self)
        self.СкоростьЗначение.setGeometry(140, 200, 150, 25)
        self.СкоростьЗначение.setFont(QFont("Georgia ", 15))
        self.СкоростьЗначение.setVisible(False)

        self.Трафик = QLabel("Трафик: ", self)
        self.Трафик.setGeometry(45, 225, 150, 25)
        self.Трафик.setFont(QFont("Georgia ", 15))
        self.Трафик.setVisible(False)
        self.ТрафикЗначение = QLabel("", self)
        self.ТрафикЗначение.setGeometry(130, 225, 150, 25)
        self.ТрафикЗначение.setFont(QFont("Georgia ", 15))
        self.ТрафикЗначение.setVisible(False)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = ОсновноеОкно()
    window.show()
    sys.exit(app.exec())