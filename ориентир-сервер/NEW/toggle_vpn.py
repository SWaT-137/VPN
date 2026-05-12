import subprocess
import sys
import os

PROXY_ADDR = "socks5://127.0.0.1:1080"
URL = "https://2ip.ru"

def get_browser():
    # Ищем, какой браузер установлен по умолчанию или проверяем популярные
    browsers = [
        
        r"C:\Users\{}\AppData\Local\Yandex\YandexBrowser\Application\browser.exe".format(os.environ.get('USERNAME'))
    ]
    
    for browser in browsers:
        if os.path.exists(browser):
            return browser
    return None

def main():
    print("[*] Поиск браузера...")
    browser_path = get_browser()
    
    if not browser_path:
        print("[!] Ошибка: Chrome, Edge или Яндекс браузер не найдены в стандартных папках!")
        print("[!] Запусти вручную: start chrome --proxy-server=\"socks5://127.0.0.1:1080\" https://2ip.ru")
        return

    print(f"[+] Найден: {browser_path.split('\\')[-1]}")
    print("[+] Запуск браузера через VPN...")
    print("[!] ВНИМАНИЕ: Как только закроешь это окно PowerShell - браузер закроется, и VPN отключится!\n")

    try:
        # Запускаем браузер с принудительным прокси
        proc = subprocess.Popen([
            browser_path,
            f"--proxy-server={PROXY_ADDR}",
            "--new-window",
            URL
        ])
        
        # Скрипт будет висеть, пока открыто окно браузера
        proc.wait()
        print("\n[-] Браузер закрыт. VPN режим деактивирован.")
        
    except Exception as e:
        print(f"[!] Ошибка запуска: {e}")

if __name__ == "__main__":
    main()