#!/usr/bin/env python3
import os
import sys
import json
import ctypes
import logging
import subprocess
import time

from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QLabel, QLineEdit, QMenu, QVBoxLayout
from PySide6.QtGui import QFont, QPainter, QColor, QAction, QPen, Qt
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QRectF, Property, Signal, QPoint, QTimer, QThread

# ==========================================
# ЛОГИКА VPN
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)-5s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

XRAY_CONFIG = {
    "server_ip": "222.167.211.76",
    "server_port": 443,
    "uuid": "f50b747e-7425-4a82-b21b-9a54995ac384",
    "public_key": "crqA0pa4xcMrI674wGGvqhv9S-cMKyM3NGs243Iw_QQ",
    "short_id": "a9b8f4a1",
    "sni": "www.microsoft.com",
    "fingerprint": "chrome"
}

TUN_NAME = "XrayVPN"
TUN_MTU = 1420
NO_WINDOW = subprocess.CREATE_NO_WINDOW


def run_shell(cmd):
    return subprocess.run(cmd, shell=True, creationflags=NO_WINDOW).returncode


def route_print():
    r = subprocess.run('route print 0.0.0.0', capture_output=True, shell=True, creationflags=NO_WINDOW)
    return r.stdout.decode('cp866', errors='replace')


def route_print_ip(ip):
    r = subprocess.run(f'route print {ip} *', capture_output=True, shell=True, creationflags=NO_WINDOW)
    return r.stdout.decode('cp866', errors='replace')


def ps_cmd(cmd):
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command', cmd], capture_output=True, creationflags=NO_WINDOW,
                           timeout=4)
        return r.stdout.decode('utf-8-sig', errors='replace').strip()
    except subprocess.TimeoutExpired:
        return ""


def format_bytes(b):
    if b < 1024:
        return f"{b} Б"
    elif b < 1024 ** 2:
        return f"{b / 1024:.1f} Кб"
    elif b < 1024 ** 3:
        return f"{b / (1024 ** 2):.1f} Мб"
    else:
        return f"{b / (1024 ** 3):.2f} Гб"


def format_speed(bps):
    bps = abs(bps)
    if bps < 1024:
        return f"{bps} Б/с"
    elif bps < 1024 ** 2:
        return f"{bps / 1024:.1f} Кб/с"
    else:
        return f"{bps / (1024 ** 2):.1f} Мб/с"


class XrayManager:
    def __init__(self):
        self.xray_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xray.exe")
        self.process = None
        self.log_file = None

    def generate_config(self, custom_short_id=None):
        if custom_short_id:
            XRAY_CONFIG["short_id"] = custom_short_id

        config = {
            "log": {"loglevel": "warning"},
            "inbounds": [{
                "tag": "tun-in", "protocol": "tun",
                "settings": {
                    "name": TUN_NAME, "mtu": TUN_MTU, "address": ["10.88.88.1/24"],
                    "gateway": "10.88.88.1", "stack": "gvisor",
                    "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}
                }
            }],
            "outbounds": [
                {
                    "tag": "proxy", "protocol": "vless",
                    "settings": {
                        "vnext": [{
                            "address": XRAY_CONFIG["server_ip"], "port": XRAY_CONFIG["server_port"],
                            "users": [{"id": XRAY_CONFIG["uuid"], "encryption": "none", "flow": "xtls-rprx-vision"}]
                        }]
                    },
                    "streamSettings": {
                        "network": "tcp", "security": "reality",
                        "realitySettings": {
                            "serverName": XRAY_CONFIG["sni"], "fingerprint": XRAY_CONFIG["fingerprint"],
                            "publicKey": XRAY_CONFIG["public_key"], "shortId": XRAY_CONFIG["short_id"]
                        }
                    }
                },
                {"tag": "direct", "protocol": "freedom", "settings": {}},
                {"tag": "block", "protocol": "blackhole", "settings": {"response": {"type": "none"}}}
            ],
            "dns": {
                "servers": [
                    {"address": "https://1.1.1.1/dns-query", "detour": "proxy"},
                    {"address": "https://8.8.8.8/dns-query", "detour": "proxy"}
                ], "queryStrategy": "UseIPv4"
            },
            "routing": {
                "domainStrategy": "IPIfNonMatch",
                "rules": [
                    {"type": "field", "ip": ["169.254.0.0/16", "224.0.0.0/4", "ff00::/8"], "outboundTag": "block"},
                    {"type": "field", "inboundTag": ["tun-in"], "port": "53", "outboundTag": "proxy"},
                    {"type": "field", "ip": ["geoip:private"], "outboundTag": "direct"}
                ]
            }
        }
        with open("xray_config.json", 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)

    def start(self):
        self.log_file = open("xray_console.log", "w", encoding='utf-8')
        self.process = subprocess.Popen(
            [self.xray_path, "run", "-c", "xray_config.json"],
            stdout=self.log_file, stderr=subprocess.STDOUT, creationflags=NO_WINDOW
        )
        logger.info(f"🚀 Xray запущен (PID: {self.process.pid})")

    def stop(self):
        if self.log_file:
            self.log_file.close()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


class RoutingManager:
    @staticmethod
    def get_tun_index():
        idx = ps_cmd(
            f'Get-NetAdapter -Name "{TUN_NAME}" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ifIndex')
        return int(idx) if idx.isdigit() else None

    @staticmethod
    def get_main_adapter_info():
        text = route_print()
        for line in text.split('\n'):
            parts = line.split()
            if len(parts) >= 5 and parts[0] == '0.0.0.0' and parts[1] == '0.0.0.0':
                gateway = parts[2]
                interface_ip = parts[3]
                idx = ps_cmd(
                    f'(Get-NetIPAddress -AddressFamily IPv4 -IPAddress "{interface_ip}" -ErrorAction SilentlyContinue).InterfaceIndex')
                if idx.isdigit():
                    return gateway, int(idx)
        return None, None

    @staticmethod
    def add_routes():
        server_ip = XRAY_CONFIG["server_ip"]
        tun_index = None
        for i in range(15):
            tun_index = RoutingManager.get_tun_index()
            if tun_index:
                break
            time.sleep(1)
            logger.info(f"⏳ Ожидание адаптера {TUN_NAME}... ({i + 1}/15)")

        if not tun_index:
            logger.error(f"❌ Адаптер {TUN_NAME} так и не появился!")
            return False

        gateway, main_if = RoutingManager.get_main_adapter_info()
        if not gateway or not main_if:
            logger.error("❌ Не удалось определить основной сетевой интерфейс!")
            return False

        run_shell('route delete 0.0.0.0 mask 128.0.0.0')
        run_shell('route delete 128.0.0.0 mask 128.0.0.0')

        rc = run_shell(f'route add {server_ip} mask 255.255.255.255 {gateway} IF {main_if} metric 1')
        if rc != 0:
            logger.error(f"❌ route add вернул код {rc}")
            return False

        rc1 = run_shell(f'route add 0.0.0.0 mask 128.0.0.0 0.0.0.0 IF {tun_index} metric 5')
        rc2 = run_shell(f'route add 128.0.0.0 mask 128.0.0.0 0.0.0.0 IF {tun_index} metric 5')
        if rc1 != 0 or rc2 != 0:
            return False

        run_shell(f'netsh interface ipv6 set interface "{TUN_NAME}" disable')
        return True

    @staticmethod
    def remove_routes():
        run_shell('route delete 0.0.0.0 mask 128.0.0.0')
        run_shell('route delete 128.0.0.0 mask 128.0.0.0')
        run_shell(f'route delete {XRAY_CONFIG["server_ip"]} mask 255.255.255.255')
        run_shell(f'netsh interface ipv6 set interface "{TUN_NAME}" enable')


def check_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


# ==========================================
# ПОТОКИ
# ==========================================
class VpnWorker(QThread):
    status_signal = Signal(str)

    def __init__(self, server_ip, server_port, short_id):
        super().__init__()
        self.server_ip = server_ip
        self.server_port = server_port
        self.short_id = short_id
        self.xray = XrayManager()
        self._is_running = True

    def run(self):
        XRAY_CONFIG["server_ip"] = self.server_ip
        XRAY_CONFIG["server_port"] = self.server_port

        try:
            self.xray.generate_config(custom_short_id=self.short_id)
            self.xray.start()

            if RoutingManager.add_routes():
                self.status_signal.emit("connected")
                while self._is_running:
                    if self.xray.process.poll() is not None:
                        self.status_signal.emit("error")
                        break
                    time.sleep(1)
            else:
                self.status_signal.emit("error")
                self.cleanup()
        except Exception as e:
            logger.error(f"Критическая ошибка: {e}")
            self.status_signal.emit("error")
            self.cleanup()

    def stop_vpn(self):
        self._is_running = False
        self.cleanup()

    def cleanup(self):
        RoutingManager.remove_routes()
        self.xray.stop()


class StatsWorker(QThread):
    stats_signal = Signal(int, str, str, int, int)  # ping, dl_speed, ul_speed, total_dl, total_ul

    def __init__(self):
        super().__init__()
        self._is_running = False
        self.prev_dl = 0
        self.prev_ul = 0
        self.total_dl = 0
        self.total_ul = 0
        self.speed_samples = []

    def run(self):
        self._is_running = True
        # Небольшая задержка перед стартом сбора статистики
        time.sleep(2)

        while self._is_running:
            try:
                cmd = f'Get-NetAdapterStatistics -Name "{TUN_NAME}" -ErrorAction SilentlyContinue | Select-Object ReceivedBytes, SentBytes | ConvertTo-Json'
                res = ps_cmd(cmd)
                if res:
                    data = json.loads(res)
                    cur_dl = int(data.get("ReceivedBytes", 0))
                    cur_ul = int(data.get("SentBytes", 0))

                    dl_speed = cur_dl - self.prev_dl
                    ul_speed = cur_ul - self.prev_ul

                    self.total_dl = cur_dl
                    self.total_ul = cur_ul
                    self.prev_dl = cur_dl
                    self.prev_ul = cur_ul

                    self.speed_samples.append(dl_speed + ul_speed)

                    # Пинг (асинхронно, 1 запрос)
                    ping_cmd = 'Test-Connection -ComputerName 1.1.1.1 -Count 1 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ResponseTime'
                    ping_res = ps_cmd(ping_cmd)
                    ping = int(ping_res) if ping_res.isdigit() else -1

                    self.stats_signal.emit(
                        ping,
                        format_speed(dl_speed),
                        format_speed(ul_speed),
                        self.total_dl,
                        self.total_ul
                    )
                else:
                    self.stats_signal.emit(-1, "0 Б/с", "0 Б/с", self.total_dl, self.total_ul)
            except Exception:
                pass

            time.sleep(1)

    def stop_stats(self):
        self._is_running = False

    def get_average_speed_str(self):
        if not self.speed_samples: return "0 Б/с"
        avg = sum(self.speed_samples) / len(self.speed_samples)
        return format_speed(avg)


# ==========================================
# ВИЗУАЛ
# ==========================================
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
        padding: 20px;
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
    traficZnachenie = 0
    Format = ""
    spedZnachenie = 0
    FormatSped = ""
    podklZnach = 1

    def __init__(self):
        super().__init__()
        self.ping = 0
        self.speed = 0
        self.speed1 = 0
        self.settings_file = "settings.json"
        self.server = ""
        self.port = 443
        self.password = ""
        self.vpn_worker = None
        self.stats_worker = None

        self.setWindowTitle("VPN")
        self.setGeometry(100, 100, 220, 280)
        self.setFixedSize(300, 500)
        self.setStyleSheet("background-color: #26252d;")

        self.toggle_button = ToggleSwitch(self)
        self.toggle_button.setGeometry(50, 200, 200, 200)
        self.toggle_button.toggled.connect(self.on_button_on)

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

        self.ping_label = QLabel("Ping: ", self)
        self.ping_label.setAlignment(Qt.AlignCenter)
        self.ping_label.setGeometry(37, 335, 225, 35)
        self.ping_label.setStyleSheet("color: #A0A0B0;")
        self.ping_label.setFont(QFont("JetBrains Mono", 12, QFont.Weight.Medium))
        self.ping_label.setVisible(False)

        self.speed_label = QLabel("Speed: ", self)
        self.speed_label.setAlignment(Qt.AlignCenter)
        self.speed_label.setGeometry(37, 365, 225, 35)
        self.speed_label.setStyleSheet("color: #A0A0B0;")
        self.speed_label.setFont(QFont("JetBrains Mono", 12, QFont.Weight.Medium))
        self.speed_label.setVisible(False)

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

    def closeEvent(self, event):
        if self.stats_worker and self.stats_worker.isRunning():
            self.stats_worker.stop_stats()
            self.stats_worker.wait(1000)
        if self.vpn_worker and self.vpn_worker.isRunning():
            self.vpn_worker.stop_vpn()
            self.vpn_worker.wait(2000)
        event.accept()

    def set_connected_ui(self):
        self.status_label.setText("Подключено")
        self.status_label.setStyleSheet("""
               color: #4CAF50; 
               font-size: 22px; 
               font-family: 'Inter';
               font-weight: 600; 
               background-color: transparent;
           """)
        self.ping_label.setText("Ping: измерение...")
        self.ping_label.setVisible(True)
        self.speed_label.setText("Speed: измерение...")
        self.speed_label.setVisible(True)
        self.timer_seconds = 0
        self.timer.start(1000)
        self.timer_label.setVisible(True)
        self.update_timer()

    def set_disconnected_ui(self):
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

    def force_toggle_off(self):
        self.toggle_button._checked = False
        self.toggle_button.animation.stop()
        self.toggle_button.animation.setStartValue(self.toggle_button._position)
        self.toggle_button.animation.setEndValue(0.0)
        self.toggle_button.animation.start()

    def on_button_on(self, is_checked):
        if is_checked:
            if not check_admin():
                self.status_label.setText("Нет прав!")
                self.force_toggle_off()
                return

            if not os.path.exists("xray.exe") or not os.path.exists("wintun.dll"):
                self.status_label.setText("Нет файлов!")
                self.force_toggle_off()
                return

            self.status_label.setText("Подключение...")
            ip = self.server if self.server else XRAY_CONFIG["server_ip"]
            port = self.port if self.port else XRAY_CONFIG["server_port"]
            short_id = self.password if self.password else XRAY_CONFIG["short_id"]

            self.vpn_worker = VpnWorker(ip, port, short_id)
            self.vpn_worker.status_signal.connect(self.on_vpn_status_changed)
            self.vpn_worker.start()
        else:
            self.status_label.setText("Отключение...")
            if self.stats_worker:
                self.stats_worker.stop_stats()
                self.stats_worker.wait(1000)
                self.stats_worker = None

            if self.vpn_worker:
                self.vpn_worker.stop_vpn()
                self.vpn_worker.wait(3000)
            self.set_disconnected_ui()

    def on_vpn_status_changed(self, status):
        if status == "connected":
            self.set_connected_ui()
            self.stats_worker = StatsWorker()
            self.stats_worker.stats_signal.connect(self.update_real_stats)
            self.stats_worker.start()
        elif status == "error":
            self.status_label.setText("Ошибка")
            self.force_toggle_off()
            self.set_disconnected_ui()
            if self.stats_worker:
                self.stats_worker.stop_stats()
                self.stats_worker = None

    def update_real_stats(self, ping, dl_speed, ul_speed, total_dl, total_ul):
        ping_str = f"Ping: {ping} ms" if ping != -1 else "Ping: таймаут"
        self.ping_label.setText(ping_str)
        self.speed_label.setText(f"Speed: ↓ {dl_speed} | ↑ {ul_speed}")

        self.traficZnachenie = total_dl + total_ul
        self.Format = ""
        self.spedZnachenie = 0
        self.FormatSped = ""

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
        position = self.button4.mapToGlobal(QPoint(0, self.button4.height()))
        menu.exec(position)

    def show_settings_overlay(self):
        content = QWidget()
        content.setLayout(None)
        content.setFixedSize(320, 500)

        self.label = QLabel("Сервер:", content)
        self.label.setGeometry(-5, 100, 100, 65)
        self.label.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")

        self.server_edit = QLineEdit(content)
        self.server_edit.setFixedSize(200, 35)
        self.server_edit.setGeometry(85, 115, 200, 20)
        self.server_edit.setText(self.server)
        self.server_edit.setPlaceholderText("Введите адрес сервера")
        self.server_edit.setStyleSheet("""
            background-color: #404040;
            color: #A0A0B0;
            border-radius: 5px;
            border: 1px solid #555555;
            padding: 2px;
        """)

        self.label2 = QLabel("Порт:", content)
        self.label2.setGeometry(-5, 150, 100, 65)
        self.label2.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")

        self.port_edit = QLineEdit(content)
        self.port_edit.setFixedSize(200, 35)
        self.port_edit.setGeometry(85, 165, 200, 20)
        self.port_edit.setPlaceholderText("По умолчанию 443")
        self.port_edit.setStyleSheet("""
            background-color: #404040;
            color: #A0A0B0;
            border-radius: 5px;
            border: 1px solid #555555;
            padding: 2px;
        """)

        self.label3 = QLabel("Short ID:", content)
        self.label3.setGeometry(-5, 200, 100, 65)
        self.label3.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")

        self.password_edit = QLineEdit(content)
        self.password_edit.setFixedSize(200, 35)
        self.password_edit.setGeometry(85, 215, 200, 20)
        self.password_edit.setText(self.password)
        self.password_edit.setPlaceholderText("Введите Short ID")
        self.password_edit.setStyleSheet("""
            background-color: #404040;
            color: #A0A0B0;
            border-radius: 5px;
            border: 1px solid #555555;
            padding: 2px;
        """)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)

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

        self.stats_trafic_label = QLabel(f"Всего использовано: расчет...", content)
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

        self.stats_speed_label = QLabel(f"Средняя скорость: расчет...", content)
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

        self.stats_connection_label = QLabel(f"Количество подключений за 24 часа: 1 \nраз", content)
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
            self.server = data["server"]
            self.port = data["port"]
            self.password = data["password"]
        else:
            data = {"server": "", "port": 443, "password": ""}
            with open("settings.json", "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)

    def save_settings(self):
        data_save = {"server": self.server, "port": self.port, "password": self.password}
        with open(self.settings_file, "w", encoding='utf-8') as f:
            json.dump(data_save, f, ensure_ascii=False, indent=4)

    def load_stats(self):
        if hasattr(self, 'stats_trafic_label') and self.stats_trafic_label:
            traffic_str = format_bytes(self.traficZnachenie) if self.traficZnachenie > 0 else "0 Б"
            self.stats_trafic_label.setText(f"Всего использовано: {traffic_str}")
            self.stats_trafic_label.setStyleSheet(
                self.stats_trafic_label.styleSheet().replace("color: #888888;", "color: white;"))

        if hasattr(self, 'stats_speed_label') and self.stats_speed_label:
            avg_str = "0 Б/с"
            if self.stats_worker and self.stats_worker.isRunning():
                avg_str = self.stats_worker.get_average_speed_str()
            self.stats_speed_label.setText(f"Средняя скорость: {avg_str}")
            self.stats_speed_label.setStyleSheet(
                self.stats_speed_label.styleSheet().replace("color: #888888;", "color: white;"))

        if hasattr(self, 'stats_connection_label') and self.stats_connection_label:
            self.stats_connection_label.setText(f"Количество подключений за 24 часа: {self.podklZnach}\nраз")
            self.stats_connection_label.setStyleSheet(
                self.stats_connection_label.styleSheet().replace("color: #888888;", "color: white;"))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())