import flet as ft
import asyncio
import os
import json

def main(page: ft.Page):
    page.title = "VPN"
    page.bgcolor = "#26252d"

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