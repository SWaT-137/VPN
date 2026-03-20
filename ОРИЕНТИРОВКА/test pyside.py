import sys
from PySide6 import QtWidgets
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QLabel
from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QFont



class ОсновноеОкно(QMainWindow):
    ТаймерОбновления = QTimer()
    def Таймер(self):
        ИмяСервера = "belarus.vpn.com"
        СтатусДанные = "Подключенно"
        ПингДанные = 50
        СкоростьДанные = 3.2
        ТрафикДанные = 100
        self.серверЗначение.setText(СтатусДанные)
        self.СтатусЗначение.setText(ИмяСервера)
        self.СтатусЗначение.setStyleSheet("""
            background-color: green;
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
            self.ТаймерОбновления.stop()

    def привтун(self):
        self.button.setStyleSheet("background-color: green;")





    def __init__(self):
        super().__init__()
        self.setFixedSize(QSize(400, 700))  # Настраиваем СЕБЯ
        self.setWindowTitle("гойда впн")
        self.setStyleSheet("background-color: #26252d")
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
        self.настройки.clicked.connect(self.привтун)
        self.настройки.setFont(QFont("Georgia", 10))
        self.настройки.setStyleSheet("""
                    background-color:  #262b2c;
                    color: white;
                    border-radius: 25px;
                """)


        self.статистика = QPushButton("Статистика", self)
        self.статистика.setGeometry(300, 600, 100, 100)
        self.статистика.setToolTip("Нажмите для открытия статистики")
        self.статистика.clicked.connect(self.привтун)
        self.статистика.setFont(QFont("Georgia", 10))
        self.статистика.setStyleSheet("""
                            background-color:  #262b2c;
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