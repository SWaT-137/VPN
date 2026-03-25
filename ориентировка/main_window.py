import sys
import json
import os
from PySide6.QtWidgets import (QApplication, QWidget, QPushButton, QDialog, 
                               QLabel, QVBoxLayout, QLineEdit, QMessageBox,QCheckBox)
from PySide6.QtGui import QColor, QPalette
from PySide6.QtCore import Slot, QSettings

class MainWindow(QWidget):
    traficZnachenie = 120  # временные переменные
    Format = "Мб"
    spedZnachenie = 2
    FormatSped = "Мб/C"
    podklZnach = 300
    VremesZnachenie = 60
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VPN")
        self.setGeometry(100, 100, 220, 280)
        self.setStyleSheet("background-color: #2F4F4F;")
        self.settings = QSettings("MyVPN", "VPNSettings")
        # Загружаем сохраненные настройки
        self.load_settings()
        self.create_info_labels()   
        # Создаем кнопку "Подключиться"
        self.button = QPushButton("Подключиться", self)
        self.button.setGeometry(10, 200, 100, 30)
        self.button.setCheckable(True)
        self.button.setChecked(False)
        self.button.clicked.connect(self.on_button_on) 
        # Кнопка "Настройка"
        self.button2 = QPushButton("Настройка", self)
        self.button2.setGeometry(110, 200, 100, 30)
        self.button2.clicked.connect(self.on_button_settings)
        # Кнопка "Статистика"
        button3 = QPushButton("Статистика", self)
        button3.setGeometry(60, 230, 100, 30)
        button3.clicked.connect(self.open_new_window_stat)
        self.ping = 30
        self.speed = 30
        self.speed1 = 30
        
        
    def setup_settings(self):
        """Настройка QSettings для сохранения в файл"""
        # Вариант 1: Сохранение в текущей директории
        self.settings = QSettings("vpn_config.ini", QSettings.Format.IniFormat)
        print(f"Настройки сохраняются в: {self.settings.fileName()}")

    def load_settings(self):
        """Загружает настройки из файла"""
        
        self.server = self.settings.value("server", "",type=str)
        self.port = self.settings.value("port", "",type=str)
        self.password = self.settings.value("password", "",type=str)
        print(f"Загружены настройки: сервер={self.server}, порт={self.port}")
    def create_info_labels(self):
        """Создает метки для отображения информации о настройках"""
        # Заголовок
        title_label = QLabel("Текущие настройки:", self)
        title_label.setGeometry(10, 10, 200, 20)
        title_label.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")
        # Метка для сервера
        self.server_label = QLabel(self)
        self.server_label.setGeometry(10, 35, 200, 20)
        self.server_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
        # Метка для порта
        self.port_label = QLabel(self)
        self.port_label.setGeometry(10, 60, 200, 20)
        self.port_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
        # Статус подключения
        self.status_label = QLabel("Статус: Не подключено", self)
        self.status_label.setGeometry(10, 150, 200, 20)
        self.status_label.setStyleSheet("color: #FFA500; font-size: 12px;")
        # Пинг
        self.ping_label = QLabel("Ping: ",self)
        self.ping_label.setGeometry(10, 95, 200, 20)
        self.ping_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
        # Скорость
        self.speed_label = QLabel("Speed: ",self)
        self.speed_label.setGeometry(10, 120, 200, 20)
        self.speed_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
        # Обновляем отображение настроек
        self.update_info_display()

    def update_info_display(self):
        """Обновляет отображение информации о настройках"""
        if self.server and self.port:
            self.server_label.setText(f"🌐 Сервер: {self.server}")
            self.port_label.setText(f"🔌 Порт: {self.port}")
        else:
            self.server_label.setText("🌐 Сервер: не настроен")
            self.port_label.setText("🔌 Порт: не настроен")
            self.server_label.setStyleSheet("color: #FFA500; font-size: 12px;")
            self.port_label.setStyleSheet("color: #FFA500; font-size: 12px;")
    # Кнопка включения/подключения
    def on_button_on(self, checked):
        if checked:
            self.button.setText("Включено")
            self.status_label.setText("Статус: Подключено")
            self.ping_label.setText(f"Ping: {self.ping} ms")
            self.speed_label.setText(f"Speed: ↓ {self.speed} | ↑ {self.speed1}")
            self.status_label.setStyleSheet("color: #4CAF50; font-size: 12px;")
            if self.server and self.port:
                print(f"Подключение к {self.server}:{self.port}")
            else:
                print("Сначала настройте подключение!")
        else:
            self.button.setText("Отключено")
            self.status_label.setText("Статус: Не подключено")
            self.ping_label.setText(f"Ping:")
            self.speed_label.setText(f"Speed:")
            self.status_label.setStyleSheet("color: #FFA500; font-size: 12px;")
    
    # Настройки
    def on_button_settings(self):
        print("Кнопка 'Настройка' нажата")
        self.open_new_window()
    
    # Открытие нового окна
    def open_new_window(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Настройки")
        dialog.setGeometry(200, 200, 300, 300)  
        dialog.setStyleSheet("background-color: #2F4F4F;")
        # Метка "Сервер"
        label = QLabel("Сервер:", dialog)
        label.setGeometry(10, 10, 100, 20)
        label.setStyleSheet("color: white; font-size: 12px;")
        # Поле для ввода сервера
        self.server_edit = QLineEdit(dialog)
        self.server_edit.setGeometry(10, 30, 220, 25)
        self.server_edit.setPlaceholderText("Введите адрес сервера")
        self.server_edit.setStyleSheet("background-color: white; color: black;")
        self.server_edit.setText(self.server)  
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
        self.port_edit.setText(self.port)  
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
        self.password_edit.setText(self.password)  
        # Кнопки 
        self.checkbox = QCheckBox("Включить кнопку",dialog)
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
        dialog = QDialog(self) #СОздаем окно
        dialog.setWindowTitle("Статистика")
        dialog.setGeometry(100, 100, 300, 250)
        dialog.setStyleSheet("background-color: #2F4F4F;")

        label = QLabel("Всего использовано мб трафика:", dialog) # Создаем лейбл с текстом
        label.setGeometry(10, 10, 200, 10)
        label.setStyleSheet("color: white; font-size: 12px;")
        trafic = QLabel(f"{self.traficZnachenie} {self.Format}", dialog) #Создаем лейбл со значением трафика
        trafic.setGeometry(210, 10, 200, 10)

        label2 = QLabel("Средняя скорость:", dialog) # Создаем лейбл с текстом
        label2.setGeometry(10, 60, 200, 20)
        label2.setStyleSheet("color: white; font-size: 12px;")
        sped = QLabel(f"{self.spedZnachenie} {self.FormatSped}", dialog)#Создаем лейбл со значением скорости
        sped.setGeometry(120, 65, 200, 10)

        label3 = QLabel("Количество подключений за 24ч:", dialog)# Создаем лейбл с текстом
        label3.setGeometry(10, 110, 200, 30)
        label3.setStyleSheet("color: white; font-size: 12px;")
        podkl = QLabel(f"{self.podklZnach} раз", dialog)#Создаем лейбл со значением кол-во подключений
        podkl.setGeometry(210, 120, 200, 10)

        label4 = QLabel("Всего времени проведено: ", dialog)# Создаем лейбл с текстом
        label4.setGeometry(10, 160, 200, 40)
        label4.setStyleSheet("color: white; font-size: 12px;")
        vremes = QLabel(f"{self.VremesZnachenie} минут", dialog)#Создаем лейбл со значением кол-во подключений
        vremes.setGeometry(180, 175, 200, 10)


        cancel_btn = QPushButton("Выход", dialog) # Кнопка выхода
        cancel_btn.setGeometry(100, 200, 100, 25)
        cancel_btn.clicked.connect(dialog.reject)

        dialog.exec()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

    