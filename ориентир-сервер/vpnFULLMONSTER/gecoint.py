import sys, os, json, subprocess, urllib.parse, base64, requests, tempfile, time, winreg
from threading import Thread

XRAY = "xray.exe"
SOCKS_PORT = 10808
PROXY_ADDR = f"127.0.0.1:{SOCKS_PORT}"


def fetch_subs(url):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "curl/8.0"})
        r.raise_for_status()
        txt = r.text.strip()
        try:
            txt = base64.b64decode(txt + "=" * (4 - len(txt) % 4)).decode("utf-8", errors="ignore")
        except:
            pass
        return [l.strip() for l in txt.splitlines() if l.startswith("vless://")]
    except Exception as e:
        print(f"❌ Ошибка подписки: {e}");
        sys.exit(1)


def parse_vless(link):
    p = urllib.parse.urlparse(link)
    uuid = p.username or p.netloc.split("@")[0]
    hp = p.netloc.split("@")[-1]
    if ":" not in hp: raise ValueError("Нет порта")
    host, port = hp.rsplit(":", 1);
    port = int(port)
    q = urllib.parse.parse_qs(p.query)
    remark = urllib.parse.unquote(p.fragment) or "vless"
    net, sec = q.get("type", ["tcp"])[0], q.get("security", ["none"])[0]
    flow, sni = q.get("flow", [None])[0], q.get("sni", [host])[0]
    path, hdr = q.get("path", ["/"])[0], q.get("host", [host])[0]
    fp = q.get("fp", ["chrome"])[0];
    pbk = q.get("pbk", [""])[0] or None;
    sid = q.get("sid", [""])[0] or None

    ob = {"tag": "proxy", "protocol": "vless", "settings": {
        "vnext": [{"address": host, "port": port, "users": [{"id": uuid, "encryption": "none", "flow": flow}]}]},
          "streamSettings": {"network": net, "security": sec}}
    if sec == "tls":
        ob["streamSettings"]["tlsSettings"] = {"serverName": sni, "allowInsecure": False, "fingerprint": fp}
    elif sec == "reality":
        ob["streamSettings"]["realitySettings"] = {"serverName": sni, "publicKey": pbk, "shortId": sid,
                                                   "fingerprint": fp or "chrome"}
    if net == "ws":
        ob["streamSettings"]["wsSettings"] = {"path": path, "headers": {"Host": hdr}}
    elif net == "grpc":
        ob["streamSettings"]["grpcSettings"] = {"serviceName": path.lstrip("/") or "grpc"}
    elif net == "tcp" and q.get("headerType", ["none"])[0].lower() == "http":
        ob["streamSettings"]["tcpSettings"] = {
            "header": {"type": "http", "request": {"path": [path], "headers": {"Host": [hdr]}}}}

    def clean(o):
        return {k: clean(v) for k, v in o.items() if v not in (None, "", [])} if isinstance(o, dict) else o

    return clean(ob), remark


def set_system_proxy(enable):
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, PROXY_ADDR if enable else "")
            winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ, "<local>")
        subprocess.run("netsh winhttp set proxy 127.0.0.1:10808" if enable else "netsh winhttp reset proxy", shell=True,
                       capture_output=True)
        subprocess.run("ipconfig /flushdns", shell=True, capture_output=True)
        print("✅ Системный прокси " + ("включён" if enable else "отключён"))
    except Exception as e:
        print(f"⚠️  Не удалось изменить прокси: {e}")


def run_xray(cfg):
    if not os.path.exists(XRAY): print(f"❌ {XRAY} не найден!"); sys.exit(1)
    path = tempfile.mktemp(suffix=".json")
    with open(path, "w") as f:
        json.dump(cfg, f)

    si = subprocess.STARTUPINFO();
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    proc = subprocess.Popen([XRAY, "-config", path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            startupinfo=si, creationflags=subprocess.CREATE_NO_WINDOW)

    def log():
        for l in proc.stdout: print(f"[Xray] {l.strip()}")

    Thread(target=log, daemon=True).start()
    return proc, path


def main():
    print("=" * 50);
    print(" VLESS Client — Windows (Стабильный режим)");
    print("=" * 50)
    inp = input("\n📥 Ссылка: ").strip()
    if not inp: sys.exit(1)

    links = fetch_subs(inp) if inp.startswith("http") else [inp]
    if not links: print("❌ Нет vless ссылок"); sys.exit(1)
    sel = links[0]
    if len(links) > 1:
        for i, l in enumerate(links, 1): print(
            f"[{i}] {urllib.parse.unquote(urllib.parse.urlparse(l).fragment or f'#{i}')}")
        try:
            sel = links[int(input("Выбор: ") or 1) - 1]
        except:
            pass

    try:
        ob, remark = parse_vless(sel)
    except Exception as e:
        print(f"❌ {e}"); sys.exit(1)

    cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"port": SOCKS_PORT, "listen": "127.0.0.1", "protocol": "socks",
                      "settings": {"auth": "noauth", "udp": True}}],
        "outbounds": [ob, {"protocol": "freedom", "tag": "direct"}],
        "routing": {
            "rules": [{"type": "field", "ip": ["127.0.0.0/8", "10.0.0.0/8", "192.168.0.0/16"], "outboundTag": "direct"},
                      {"type": "field", "network": "tcp,udp", "outboundTag": "proxy"}]}
    }

    print(f"🚀 {remark}");
    print("⏳ Запуск...")
    proc, cfg_path = run_xray(cfg)
    time.sleep(2)

    try:
        set_system_proxy(True)
        ip = requests.get("https://api.ipify.org", timeout=5,
                          proxies={"http": f"socks5://{PROXY_ADDR}", "https": f"socks5://{PROXY_ADDR}"}).text
        print(f"🌍 Ваш IP через VPN: {ip}")
        print("🔵 Прокси активен. Закройте консоль или нажмите Enter для отключения.")
        input()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"⚠️  Проверка: {e}")
    finally:
        print("\n🛑 Отключение...")
        set_system_proxy(False)
        try:
            proc.terminate(); proc.wait(3)
        except:
            proc.kill()
        if os.path.exists(cfg_path): os.remove(cfg_path)
        print("✅ Готово.")


if __name__ == "__main__":
    main()

