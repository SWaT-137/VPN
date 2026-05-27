import os
import sys
import json
import ctypes
import subprocess
import time
import socket
import urllib.request
import urllib.parse
import base64
import re

from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QLabel, QLineEdit, QMenu, QVBoxLayout, QMessageBox
from PySide6.QtGui import QFont, QPainter, QColor, QAction, QPen, Qt
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QRectF, Property, Signal, QPoint, QTimer, QThread

TUN_NAME = "XrayVPN"
TUN_MTU = 1420
NO_WINDOW = subprocess.CREATE_NO_WINDOW

def resource_path(relative_path):
    """ Получить абсолютный путь к файлу, работает и для разработки, и для PyInstaller """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

def run_shell(cmd):
    return subprocess.run(cmd, shell=True, creationflags=NO_WINDOW).returncode


def route_print():
    r = subprocess.run('route print 0.0.0.0', capture_output=True, shell=True, creationflags=NO_WINDOW)
    return r.stdout.decode('cp866', errors='replace')


def ps_cmd(cmd):
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command', cmd], capture_output=True, creationflags=NO_WINDOW,
                           timeout=4)
        return r.stdout.decode('utf-8-sig', errors='replace').strip()
    except subprocess.TimeoutExpired:
        return ""


def check_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def fetch_subscription(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read().decode('utf-8', errors='replace').strip()
    except Exception as e:
        raise Exception(f"Не удалось загрузить подписку: {e}")

    try:
        decoded = base64.b64decode(raw, validate=True).decode('utf-8', errors='replace')
        lines = decoded.strip().splitlines()
        if lines:
            raw = decoded
    except Exception:
        pass

    links = [line.strip() for line in raw.splitlines() if line.strip().startswith('vless://')]
    if not links:
        raise Exception("В подписке не найдено vless-ссылок.")
    return links


def parse_vless_link(link):
    if not link.startswith('vless://'):
        raise ValueError("Неверный формат ссылки")
    rest = link[8:]
    if '#' in rest:
        rest, name = rest.split('#', 1)
        name = urllib.parse.unquote(name)
    else:
        name = 'VLESS'

    if '?' in rest:
        user_part, query_part = rest.split('?', 1)
    else:
        user_part = rest
        query_part = ''

    if '@' not in user_part:
        raise ValueError("Неверный формат: нет '@'")
    uuid, host_port = user_part.split('@', 1)
    if ':' in host_port:
        host, port_str = host_port.rsplit(':', 1)
        port = int(port_str)
    else:
        host = host_port
        port = 443

    params = {}
    if query_part:
        for kv in query_part.split('&'):
            if '=' not in kv: continue
            k, v = kv.split('=', 1)
            params[k] = urllib.parse.unquote(v)

    return {
        "address": host, "port": port, "uuid": uuid,
        "public_key": params.get('pbk', ''), "short_id": params.get('sid', ''),
        "sni": params.get('sni', ''), "fingerprint": params.get('fp', 'chrome'),
        "spiderX": params.get('spx', '/'), "encryption": params.get('encryption', 'none'),
        "flow": params.get('flow', ''), "remarks": urllib.parse.unquote(name)
    }


class XrayManager:
    def __init__(self, config):
        self.xray_path = resource_path("xray.exe")
        self.config = config
        self.process = None
        self.log_file = None
        self.server_ip = None

    def generate_config(self):
        try:
            self.server_ip = socket.gethostbyname(self.config['address'])
        except socket.gaierror as e:
            raise Exception(f"Не удалось разрешить адрес {self.config['address']}: {e}")

        xray_conf = {
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
                            "address": self.config['address'], "port": self.config['port'],
                            "users": [{"id": self.config['uuid'], "encryption": self.config.get('encryption', 'none')}]
                        }]
                    },
                    "streamSettings": {
                        "network": "tcp", "security": "reality",
                        "realitySettings": {
                            "serverName": self.config['sni'], "fingerprint": self.config['fingerprint'],
                            "publicKey": self.config['public_key'], "shortId": self.config['short_id'],
                            "spiderX": self.config['spiderX']
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
                    {"type": "field", "ip": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"],
                     "outboundTag": "direct"}
                ]
            }
        }

        script_dir = os.path.dirname(self.xray_path)
        config_path = os.path.join(script_dir, "xray_config.json")
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(xray_conf, f, indent=2)

    def start(self):
        script_dir = os.path.dirname(self.xray_path)
        log_path = os.path.join(script_dir, "xray_console.log")
        config_path = os.path.join(script_dir, "xray_config.json")

        self.log_file = open(log_path, "w", encoding='utf-8')
        self.process = subprocess.Popen(
            [self.xray_path, "run", "-c", config_path],
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            cwd=script_dir,
            creationflags=NO_WINDOW
        )


    def stop(self):
        if self.log_file:
            try:
                self.log_file.close()
            except:
                pass
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
    def add_routes(server_ip):
        tun_index = None
        for i in range(15):
            tun_index = RoutingManager.get_tun_index()
            if tun_index: break
            time.sleep(1)

        if not tun_index:
            raise Exception(f"Адаптер {TUN_NAME} так и не появился!")

        gateway, main_if = RoutingManager.get_main_adapter_info()
        if not gateway or not main_if:
            raise Exception("Не удалось определить основной сетевой интерфейс!")

        run_shell('route delete 0.0.0.0 mask 128.0.0.0')
        run_shell('route delete 128.0.0.0 mask 128.0.0.0')

        if run_shell(f'route add {server_ip} mask 255.255.255.255 {gateway} IF {main_if} metric 1') != 0:
            raise Exception("Не удалось добавить маршрут до сервера")

        if run_shell(f'route add 0.0.0.0 mask 128.0.0.0 0.0.0.0 IF {tun_index} metric 5') != 0 or \
                run_shell(f'route add 128.0.0.0 mask 128.0.0.0 0.0.0.0 IF {tun_index} metric 5') != 0:
            raise Exception("Не удалось добавить маршруты через TUN")

        run_shell(f'netsh interface ipv6 set interface "{TUN_NAME}" disable')
        return True

    @staticmethod
    def remove_routes(server_ip):
        run_shell('route delete 0.0.0.0 mask 128.0.0.0')
        run_shell('route delete 128.0.0.0 mask 128.0.0.0')
        if server_ip:
            run_shell(f'route delete {server_ip} mask 255.255.255.255')
        run_shell(f'netsh interface ipv6 set interface "{TUN_NAME}" enable')


class ConnectionThread(QThread):
    success = Signal(str)
    error = Signal(str)
    status_msg = Signal(str)

    def __init__(self, link):
        super().__init__()
        self.link = link
        self.xray = None

    def run(self):
        try:
            self.status_msg.emit("Парсинг ссылки...")
            if self.link.startswith('vless://'):
                links = [self.link]
            else:
                links = fetch_subscription(self.link)

            config = parse_vless_link(links[0])
            self.xray = XrayManager(config)

            self.status_msg.emit("Генерация конфигурации...")
            self.xray.generate_config()

            self.status_msg.emit("Запуск Xray...")
            self.xray.start()
            time.sleep(2)

            if self.xray.process.poll() is not None:
                raise Exception("Xray упал сразу после запуска. Проверьте xray_console.log")

            self.status_msg.emit("Настройка маршрутов")
            RoutingManager.add_routes(self.xray.server_ip)

            self.success.emit(self.xray.server_ip)
        except Exception as e:
            self.error.emit(str(e))
            if self.xray:
                self.xray.stop()


class StatsThread(QThread):
    stats_updated = Signal(int, float, float, float, float)

    def __init__(self, server_ip):
        super().__init__()
        self._running = True
        self.last_down = 0
        self.last_up = 0
        self.server_ip = server_ip
        self.last_ping = 0

    def run(self):
        while self._running:
            ping = self.get_ping()
            down_sp, up_sp, total_down, total_up = self.get_network_stats()
            self.stats_updated.emit(ping, down_sp, up_sp, total_down, total_up)
            time.sleep(1)

    def get_ping(self):
        targets = [("1.1.1.1", 443), ("8.8.8.8", 53)]
        for host, port in targets:
            try:
                start = time.perf_counter()
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect((host, port))
                s.close()
                ping_val = int((time.perf_counter() - start) * 1000)
                self.last_ping = ping_val
                return ping_val
            except Exception:
                continue
        return self.last_ping

    def get_network_stats(self):
        cmd = f'Get-NetAdapterStatistics -Name "{TUN_NAME}" -ErrorAction SilentlyContinue | Select-Object ReceivedBytes, SentBytes | ConvertTo-Json'
        try:
            res = ps_cmd(cmd)
            if res:
                data = json.loads(res)
                cur_down = int(data.get("ReceivedBytes", 0))
                cur_up = int(data.get("SentBytes", 0))
                down_speed = max(0, (cur_down - self.last_down) / 1024)
                up_speed = max(0, (cur_up - self.last_up) / 1024)
                self.last_down = cur_down
                self.last_up = cur_up
                return down_speed, up_speed, cur_down / (1024 * 1024), cur_up / (1024 * 1024)
        except:
            pass
        return 0.0, 0.0, 0.0, 0.0

    def stop(self):
        self._running = False


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
    def position(self): return self._position

    @position.setter
    def position(self, value):
        self._position = value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(Qt.NoPen))
        width, height = self.width(), self.height()
        radius = height // 2
        circle_size = height - 12
        max_x = width - circle_size - 6
        x = 6 + self._position * max_x
        y = 6
        bg_color = QColor(76, 175, 80) if self._checked else QColor(200, 200, 200)
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
        self.setStyleSheet("background-color: #26252d;")
        self.animation = QPropertyAnimation(self, b"position")
        self.animation.setDuration(300)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.container = QWidget(self)
        self.container.setStyleSheet("color: white; background-color: #26252d; border-radius: 10px;")
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
        if not self.main_window: return
        overlay_width, overlay_height = self.width(), self.height()
        container_width, container_height = self.container.width(), self.container.height()
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
        if not self.container_layout: return
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            widget = item.widget()
            if widget is not None: widget.deleteLater()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.ping = 0
        self.speed = 0.0
        self.speed1 = 0.0
        self.total_down = 0.0
        self.total_up = 0.0

        self.settings_file = "settings.json"
        self.subscription_link = ""
        self.xray_manager = None
        self.server_ip = None
        self.is_connected = False
        self.connection_thread = None
        self.stats_thread = None

        self.setWindowTitle("VPN")
        self.setGeometry(100, 100, 220, 280)
        self.setFixedSize(300, 500)
        self.setStyleSheet("background-color: #26252d;")

        self.toggle_button = ToggleSwitch(self)
        self.toggle_button.setGeometry(50, 100, 200, 200)
        self.toggle_button.toggled.connect(self.on_button_on)

        self.button4 = QPushButton("⋮", self)
        self.button4.setGeometry(285, 0, 10, 30)
        self.button4.setFixedSize(15, 40)
        self.button4.setFont(QFont("Inter", 30))
        self.button4.setStyleSheet("""
            QPushButton { background-color: transparent; border: none; color:white }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.08); }
        """)
        self.button4.clicked.connect(self.show_menu)

        self.status_label = QLabel("Отключено", self)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setGeometry(37, 310, 225, 30)
        self.status_label.setStyleSheet(
            "color: #A0A0B0; font-size: 22px; font-family: 'Inter'; font-weight: 500; background-color: transparent;")

        self.ping_label = QLabel("Ping: ", self)
        self.ping_label.setAlignment(Qt.AlignCenter)
        self.ping_label.setGeometry(37, 335, 250, 35)
        self.ping_label.setStyleSheet("color: #A0A0B0;")
        self.ping_label.setFont(QFont("JetBrains Mono", 12, QFont.Weight.Medium))
        self.ping_label.setVisible(False)

        self.speed_label = QLabel("Speed: ", self)
        self.speed_label.setAlignment(Qt.AlignCenter)
        self.speed_label.setGeometry(35, 365, 250, 35)
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

    def closeEvent(self, event):
        if self.is_connected:
            self.force_disconnect()
        event.accept()

    def force_disconnect(self):
        if self.stats_thread:
            self.stats_thread.stop()
            self.stats_thread.wait(1000)

        if self.server_ip:
            RoutingManager.remove_routes(self.server_ip)

        if self.xray_manager:
            self.xray_manager.stop()

        self.is_connected = False
        self.server_ip = None
        self.xray_manager = None

    def update_timer(self):
        self.timer_seconds += 1
        hours = self.timer_seconds // 3600
        minutes = (self.timer_seconds % 3600) // 60
        seconds = self.timer_seconds % 60
        self.timer_label.setText(f"Время подключения: {hours:02d}:{minutes:02d}:{seconds:02d}")

    def on_button_on(self, is_checked):
        if is_checked:
            if not self.subscription_link:
                QMessageBox.warning(self, "Внимание", "Сначала укажите ссылку подписки в настройках (⋮ -> Настройки)")
                self.toggle_button._checked = False
                self.toggle_button.animation.stop()
                self.toggle_button.animation.setStartValue(self.toggle_button._position)
                self.toggle_button.animation.setEndValue(0.0)
                self.toggle_button.animation.start()
                return

            self.status_label.setText("Подключение...")
            self.status_label.setStyleSheet(
                "color: #FFC107; font-size: 22px"
                "; font-family: 'Inter'; font-weight: 500; background-color: transparent;")
            self.button4.setEnabled(False)

            self.connection_thread = ConnectionThread(self.subscription_link)
            self.connection_thread.success.connect(self.on_connection_success)
            self.connection_thread.error.connect(self.on_connection_error)
            self.connection_thread.status_msg.connect(lambda msg: self.status_label.setText(msg))
            self.connection_thread.start()
        else:
            self.force_disconnect()
            self.timer.stop()
            self.status_label.setText("Отключено")
            self.status_label.setStyleSheet(
                "color: #A0A0B0; font-size: 22px; font-family: 'Inter'; font-weight: 500; background-color: transparent;")
            self.ping_label.setVisible(False)
            self.speed_label.setVisible(False)
            self.timer_label.setVisible(False)
            self.button4.setEnabled(True)

    def on_connection_success(self, ip):
        self.xray_manager = self.connection_thread.xray
        self.server_ip = ip
        self.is_connected = True
        self.button4.setEnabled(True)

        self.status_label.setText("Подключено")
        self.status_label.setStyleSheet(
            "color: #4CAF50; font-size: 22px; font-family: 'Inter'; font-weight: 600; background-color: transparent;")

        self.ping_label.setVisible(True)
        self.speed_label.setVisible(True)
        self.timer_label.setVisible(True)

        self.timer_seconds = 0
        self.timer.start(1000)

        self.stats_thread = StatsThread(ip)
        self.stats_thread.stats_updated.connect(self.update_stats)
        self.stats_thread.start()

    def on_connection_error(self, err):
        QMessageBox.critical(self, "Ошибка подключения", err)
        self.button4.setEnabled(True)
        self.toggle_button._checked = False
        self.toggle_button.animation.stop()
        self.toggle_button.animation.setStartValue(self.toggle_button._position)
        self.toggle_button.animation.setEndValue(0.0)
        self.toggle_button.animation.start()
        self.status_label.setText("Отключено")
        self.status_label.setStyleSheet(
            "color: #A0A0B0; font-size: 22px; font-family: 'Inter'; font-weight: 500; background-color: transparent;")

    def update_stats(self, ping, down_sp, up_sp, t_down, t_up):
        self.total_down = t_down
        self.total_up = t_up

        ping_str = f"Ping: {ping} ms" if ping > 0 else "Ping: ..."
        self.ping_label.setText(ping_str)

        def fmt_speed(val):
            if val > 1024: return f"{val / 1024:.1f} Мб/с"
            return f"{val:.1f} Кб/с"

        self.speed_label.setText(f"Speed: ↓ {fmt_speed(down_sp)} | ↑ {fmt_speed(up_sp)}")

    def show_menu(self):
        menu = QMenu(self)
        act1 = QAction("⚙️Настройки", self)
        act1.triggered.connect(self.show_settings_overlay)
        menu.addAction(act1)

        act2 = QAction("📊Статистика", self)
        act2.triggered.connect(self.show_stats_overlay)
        menu.addAction(act2)

        menu.setStyleSheet("""
            QMenu { background-color: #2e2d38; border: 1px solid #3a3944; border-radius: 10px; padding: 5px; color: #A0A0B0; font-family: 'Inter'; font-size: 13px; }
            QMenu::item { padding: 8px 25px; border-radius: 6px; margin: 2px 5px; }
            QMenu::item:selected { background-color: rgba(255, 255, 255, 0.08); color: white; }
        """)
        position = self.button4.mapToGlobal(QPoint(0, self.button4.height()))
        menu.exec(position)

    def show_settings_overlay(self):
        content = QWidget()
        content.setLayout(None)
        content.setFixedSize(320, 500)

        sub_label = QLabel("Ссылка для подписки:", content)
        sub_label.setGeometry(10, 180, 280, 30)
        sub_label.setStyleSheet("color: white; font-size: 20px; font-weight: bold;")

        self.sub_link_edit = QLineEdit(content)
        self.sub_link_edit.setFixedSize(280, 35)
        self.sub_link_edit.setGeometry(10, 215, 280, 35)
        self.sub_link_edit.setText(self.subscription_link)
        self.sub_link_edit.setPlaceholderText("Вставьте ссылку")
        self.sub_link_edit.setStyleSheet(
            "background-color: #404040; color: #A0A0B0; border-radius: 5px; border: 1px solid #555555; padding: 2px 5px;")

        save_button = QPushButton("Сохранить", content)
        save_button.setCursor(Qt.PointingHandCursor)
        save_button.setGeometry(100, 270, 120, 45)
        save_button.setFixedSize(120, 45)
        save_button.setStyleSheet(
            "background-color: #4CAF50; color: white; border: none; border-radius: 5px; padding: 5px; font-weight: bold; font-family: 'Inter';")
        save_button.clicked.connect(self.save_dialog_settings)

        self.fix_button = QPushButton("🔧 Починка сети", content)
        self.fix_button.setCursor(Qt.PointingHandCursor)
        self.fix_button.setGeometry(60, 340, 200, 45)
        self.fix_button.setFixedSize(200, 45)
        self.fix_button.setStyleSheet("""
            background-color: #FF5722; 
            color: white;
            border: none;
            border-radius: 5px;
            padding: 5px;
            font-weight: bold;
            font-family: 'Inter';
        """)
        self.fix_button.clicked.connect(self.force_fix_network)

        cancel_button = QPushButton("←", content)
        cancel_button.setCursor(Qt.PointingHandCursor)
        cancel_button.setGeometry(10, 10, 35, 35)
        cancel_button.setFixedSize(35, 35)
        cancel_button.setStyleSheet("""
            QPushButton { background-color: transparent; color: white; border-radius: 17px; padding: 0px; padding-top: -3px; font-size: 22px; font-family: 'Inter' }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.05); }
        """)
        cancel_button.clicked.connect(self.overlay.hide_overlay)
        self.overlay.show_with_content(content)

    def save_dialog_settings(self):
        self.subscription_link = self.sub_link_edit.text()
        self.save_settings()
        self.overlay.hide_overlay()

    def force_fix_network(self):
        reply = QMessageBox.question(
            self, 'Принудительная починка',
            'Это действие принудительно завершит Xray, удалит маршруты\n'
            'и отключит зависший виртуальный адаптер.\n'
            'Используйте, если при попытке включения ошибка.\n\nПродолжить?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            run_shell('taskkill /F /IM xray.exe >nul 2>&1')
            run_shell('route delete 0.0.0.0 mask 128.0.0.0 >nul 2>&1')
            run_shell('route delete 128.0.0.0 mask 128.0.0.0 >nul 2>&1')
            ps_cmd(f'Disable-NetAdapter -Name "{TUN_NAME}" -Confirm:$false -ErrorAction SilentlyContinue')

            if self.is_connected or self.toggle_button._checked:
                self.is_connected = False
                self.server_ip = None
                self.xray_manager = None

                if self.stats_thread:
                    self.stats_thread.stop()
                    self.stats_thread = None

                self.timer.stop()

                self.toggle_button._checked = False
                self.toggle_button.animation.stop()
                self.toggle_button.animation.setStartValue(self.toggle_button._position)
                self.toggle_button.animation.setEndValue(0.0)
                self.toggle_button.animation.start()

                self.status_label.setText("Отключено")
                self.status_label.setStyleSheet(
                    "color: #A0A0B0; font-size: 22px; font-family: 'Inter'; font-weight: 500; background-color: transparent;")
                self.ping_label.setVisible(False)
                self.speed_label.setVisible(False)
                self.timer_label.setVisible(False)

            QMessageBox.information(self, "Готово", "Починка выполнена.")

    def show_stats_overlay(self):
        content = QWidget()
        content.setLayout(None)
        content.setFixedSize(300, 500)

        total_mb = self.total_down + self.total_up
        self.stats_trafic_label = QLabel(f"Всего использовано: {total_mb:.2f} Мб", content)
        self.stats_trafic_label.setAlignment(Qt.AlignCenter)
        self.stats_trafic_label.setFixedSize(300, 70)
        self.stats_trafic_label.setGeometry(2, 115, 20, 10)
        self.stats_trafic_label.setStyleSheet(
            "color: white; font-size: 14px; margin: 5px; padding: 10px; background-color: #3a3944; border-radius: 8px;")

        self.stats_speed_label = QLabel(f"Загружено: {self.total_down:.2f} Мб\nОтправлено: {self.total_up:.2f} Мб",
                                        content)
        self.stats_speed_label.setAlignment(Qt.AlignCenter)
        self.stats_speed_label.setFixedSize(300, 70)
        self.stats_speed_label.setGeometry(2, 195, 20, 10)
        self.stats_speed_label.setStyleSheet(
            "color: white; font-size: 14px; margin: 5px; padding: 10px; background-color: #3a3944; border-radius: 8px;")

        hours = self.timer_seconds // 3600
        minutes = (self.timer_seconds % 3600) // 60
        seconds = self.timer_seconds % 60
        time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        self.stats_connection_label = QLabel(f"Текущая сессия: {time_str}", content)
        self.stats_connection_label.setAlignment(Qt.AlignCenter)
        self.stats_connection_label.setFixedSize(300, 70)
        self.stats_connection_label.setGeometry(2, 275, 20, 10)
        self.stats_connection_label.setStyleSheet(
            "color: white; font-size: 14px; margin: 5px; padding: 10px; background-color: #3a3944; border-radius: 8px;")

        exit_button = QPushButton("←", content)
        exit_button.setCursor(Qt.PointingHandCursor)
        exit_button.setGeometry(10, 10, 35, 35)
        exit_button.setFixedSize(35, 35)
        exit_button.setStyleSheet("""
            QPushButton { background-color: transparent; color: white; border-radius: 17px; padding: 0px; padding-top: -3px; font-size: 22px; font-family: 'Inter' }
            QPushButton:hover { background-color: rgba(255, 255, 255, 0.05); }
        """)
        exit_button.clicked.connect(self.overlay.hide_overlay)
        self.overlay.show_with_content(content)

    def load_settings(self):
        if os.path.exists(self.settings_file):
            with open(self.settings_file, "r", encoding='utf-8') as f:
                data = json.load(f)
            self.subscription_link = data.get("subscription_link", "")
        else:
            self.save_settings()

    def save_settings(self):
        with open(self.settings_file, "w", encoding='utf-8') as f:
            json.dump({"subscription_link": self.subscription_link}, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())