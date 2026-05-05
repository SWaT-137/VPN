import flet as ft

def main(page: ft.Page):
    page.title = "VPN"
    page.bgcolor = "#26252d"

    status_text = ft.Text("Отключено", color ="#A0A0B0", size = 24, text_align = ft.TextAlign.CENTER)
    ping_text = ft.Text("Ping: 30 ms", color = "#A0A0B0", size = 14, text_align = ft.TextAlign.CENTER, visible = False)
    switch = ft.Switch(active_color="#4CAF50")

    def on_toggle(e):
        if switch.value:
            status_text.value = "Подключено"
            status_text.color = "#4CAF50"
            ping_text.visible = True
        else:
            status_text.value = "Отключено"
            status_text.color = "#A0A0B0"
            ping_text.visible = False

        page.update()

    switch.on_change = on_toggle

    column = ft.Column(
        controls = [switch,status_text, ping_text],
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