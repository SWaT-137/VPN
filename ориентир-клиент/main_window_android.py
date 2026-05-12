from encodings import utf_8

import flet as ft
import asyncio
import os
import json

settings_file = "settings_android.json"

def main(page: ft.Page):
    page.title = "VPN"
    page.bgcolor = "#26252d"

    def load_settings():
        # Если файл существует, пытаемся его прочитать
        if os.path.exists(settings_file):
            try:
                with open(settings_file, "r", encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:

                return {"server": "", "port": "443", "password": ""}

        return {"server": "", "port": "443", "password": ""}

    current_settings = load_settings()

    status_text = ft.Text("Отключено", color ="#A0A0B0", size = 24, text_align = ft.TextAlign.CENTER)
    ping_text = ft.Text("Ping: ", color = "#A0A0B0", size = 14, text_align = ft.TextAlign.CENTER)
    speed_text = ft.Text("Speed: ", color = "#A0A0B0", size = 14, text_align = ft.TextAlign.CENTER)
    timer_text = ft.Text("Время подключения: 00:00:00", color="#A0A0B0", size=14, text_align=ft.TextAlign.CENTER)

    switch = ft.Switch(
        active_color="#4CAF50", scale = ft.Scale(scale=1.4))

    stats = ft.Container(
        content = ft.Column(
            controls = [ping_text, speed_text, timer_text],
            alignment = ft.MainAxisAlignment.CENTER,
            horizontal_alignment = ft.CrossAxisAlignment.CENTER,
            spacing = 5
        ),
        opacity = 0,
        animate_opacity = ft.Animation(250, "easeOut")
    )

    timer_seconds = 0
    is_connected = False

    def close_dialog(dlg):
        dlg.open = False
        page.update()

    def open_settings():
        server = ft.TextField(
            label = "Сервер",
            value=current_settings["server"],
            hint_text = "Введите адрес сервера",
            bgcolor = "#404040",
            color = "#A0A0B0",
            border_color = "#555555",
            focused_border_color = "#4CAF50"
        )

        port = ft.TextField(
            label="Порт",
            value = str(current_settings["port"]),
            hint_text="По умолчанию 443",
            bgcolor="#404040",
            color="#A0A0B0",
            border_color="#555555",
            focused_border_color="#4CAF50"
        )

        password = ft.TextField(
            label="Пароль",
            value=current_settings["password"],
            password=True, can_reveal_password=True,
            hint_text="Введите пароль",
            bgcolor="#404040",
            color="#A0A0B0",
            border_color="#555555",
            focused_border_color="#4CAF50"
        )

        def save_settings(e = None):
            current_settings["server"] = server.value
            current_settings["port"] = port.value if port.value else "443"
            current_settings["password"] = password.value

            with open(settings_file, "w", encoding = 'utf_8') as f:
                json.dump(current_settings, f, ensure_ascii = False, indent = 4)

            close_dialog(dialog)

        dialog = ft.AlertDialog(
            bgcolor = "#2e2d38",
            title = ft.Text("⚙️ Настройки", color="white", weight=ft.FontWeight.BOLD),
            content = ft.Column(
                controls = [server, port, password],
                tight=True,
                spacing=15
            ),
            actions = [
                ft.TextButton(ft.Text("Отмена", color="#A0A0B0"), on_click = lambda: close_dialog(dialog)),
                ft.FilledButton(ft.Text("Сохранить", color="white"),on_click = save_settings, bgcolor="#4CAF50")
            ],
            actions_alignment = ft.MainAxisAlignment.END
        )

        page.overlay.append(dialog)
        dialog.open = True
        page.update()

    def open_stats(e=None):
        trafic_val = 120
        trafic_format = "Мб"
        speed_val = 2
        speed_format = "Мб/C"
        connections_val = 300

        dialog = ft.AlertDialog(
            bgcolor="#2e2d38",
            title=ft.Text("📊 Статистика", color="white", weight=ft.FontWeight.BOLD),
            content=ft.Column(
                controls=[
                    ft.Text(f"Всего использовано: {trafic_val} {trafic_format}", color="white", size=14),
                    ft.Text(f"Средняя скорость: {speed_val} {speed_format}", color="white", size=14),
                    ft.Text(f"Количество подключений за 24 часа: {connections_val} раз", color="white", size=14),
                ],
                tight=True,
                spacing=15
            ),
            actions=[
                ft.FilledButton(ft.Text("Закрыть", color="white"), on_click=lambda _: close_dialog(dialog),
                                bgcolor="#404040")
            ],
            actions_alignment=ft.MainAxisAlignment.CENTER,
        )

        page.overlay.append(dialog)
        dialog.open = True
        page.update()


    async def update_timer():
        nonlocal timer_seconds

        while is_connected:
            await asyncio.sleep(1)
            timer_seconds += 1

            hours = timer_seconds // 3600
            minutes = (timer_seconds % 3600) // 60
            seconds = timer_seconds % 60

            timer_text.value = f"Время подключения: {hours:02d}:{minutes:02d}:{seconds:02d}"
            page.update()

    def on_toggle():
        nonlocal is_connected
        nonlocal timer_seconds

        if switch.value:
            is_connected = True
            timer_seconds = 0
            timer_text.value = "Время подключения: 00:00:00"

            status_text.value = "Подключено"
            status_text.color = "#4CAF50"
            stats.opacity = 1

            asyncio.create_task(update_timer())
        else:
            is_connected = False
            status_text.value = "Отключено"
            status_text.color = "#A0A0B0"
            stats.opacity = 0

        page.update()

    switch.on_change = on_toggle


    page.appbar = ft.AppBar(
        bgcolor = ft.Colors.TRANSPARENT,
        elevation = 0,
        actions = [
            ft.PopupMenuButton(
                icon = ft.Icons.MORE_VERT,
                icon_color = "white",
                items = [
                    ft.PopupMenuItem(content = ft.Text("⚙️ Настройки"), on_click = open_settings),
                    ft.PopupMenuItem(content = ft.Text("📊 Статистика"), on_click = open_stats),
                ]
            )
        ]
    )


    column = ft.Column(
        controls = [switch, status_text, stats],
        alignment = ft.MainAxisAlignment.CENTER,
        horizontal_alignment = ft.CrossAxisAlignment.CENTER,
    )

    main_container = ft.Container(
        expand = True,
        content = column,
        alignment = ft.Alignment.CENTER,
    )
    page.add(main_container)

ft.run(main)