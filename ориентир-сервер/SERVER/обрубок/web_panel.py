#!/usr/bin/env python3
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
import hashlib
import secrets
from database import VpnDatabase

app = FastAPI(title="Geocint VPN Control Center")
db = VpnDatabase()

# --- HTML ДАШБОРД (Dark Theme) ---
HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Geocint VPN | Control Center</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0d1117; color: #c9d1d9; margin: 0; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #58a6ff; border-bottom: 1px solid #21262d; padding-bottom: 10px; }
        .card { background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 8px 24px rgba(0,0,0,0.4); }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-box { background-color: #0d1117; border: 1px solid #30363d; padding: 15px; border-radius: 6px; text-align: center; }
        .stat-value { font-size: 24px; font-weight: bold; color: #58a6ff; }
        .stat-label { font-size: 12px; color: #8b949e; text-transform: uppercase; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #21262d; }
        th { color: #8b949e; }
        .status-online { color: #3fb950; font-weight: bold; }
        .btn { background-color: #238636; color: white; border: none; padding: 10px 15px; border-radius: 6px; cursor: pointer; font-weight: bold; }
        .btn:hover { background-color: #2ea043; }
        .btn-danger { background-color: #da3633; }
        .btn-danger:hover { background-color: #f85149; }
        input { background-color: #0d1117; border: 1px solid #30363d; color: #c9d1d9; padding: 10px; border-radius: 6px; width: 200px; }
        .token-box { background-color: #0d1117; border: 1px solid #238636; color: #3fb950; padding: 15px; border-radius: 6px; font-family: monospace; word-break: break-all; margin-top: 10px; display: none; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🛡️ Geocint VPN Control Center</h1>
        
        <div class="stats-grid">
            <div class="stat-box">
                <div class="stat-value" id="online-count">0</div>
                <div class="stat-label">Онлайн сейчас</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="total-users">0</div>
                <div class="stat-label">Всего клиентов</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="total-rx">0 MB</div>
                <div class="stat-label">Загружено (RX)</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="total-tx">0 MB</div>
                <div class="stat-label">Отдано (TX)</div>
            </div>
        </div>

        <div class="card">
            <h3>➕ Добавить нового клиента</h3>
            <div style="display: flex; gap: 10px; align-items: center;">
                <input type="text" id="new-user-name" placeholder="Имя клиента (например: Ivan)">
                <button class="btn" onclick="addUser()">Сгенерировать токен</button>
            </div>
            <div class="token-box" id="token-display">
                <strong>Скопируйте токен и передайте клиенту:</strong><br>
                <span id="token-text"></span>
            </div>
        </div>

        <div class="card">
            <h3>📊 Активные сессии (Онлайн)</h3>
            <table>
                <thead>
                    <tr><th>Имя</th><th>VPN IP</th><th>Загружено</th><th>Отдано</th><th>Подключен</th></tr>
                </thead>
                <tbody id="sessions-tbody">
                    <tr><td colspan="5" style="text-align:center">Нет активных подключений</td></tr>
                </tbody>
            </table>
        </div>

        <div class="card">
            <h3>👥 Управление клиентами</h3>
            <table>
                <thead>
                    <tr><th>Имя</th><th>Дата создания</th><th>Действие</th></tr>
                </thead>
                <tbody id="users-tbody"></tbody>
            </table>
        </div>
    </div>

    <script>
        function formatBytes(bytes) {
            if (bytes === 0) return '0 Bytes';
            let k = 1024, sizes = ['Bytes', 'KB', 'MB', 'GB'];
            let i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }

        async function loadData() {
            const statsRes = await fetch('/api/stats');
            const stats = await statsRes.json();
            
            document.getElementById('online-count').innerText = stats.active_sessions.length;
            document.getElementById('total-users').innerText = stats.total_users;
            
            let totalRx = 0, totalTx = 0;
            const tbody = document.getElementById('sessions-tbody');
            tbody.innerHTML = '';
            
            if (stats.active_sessions.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" style="text-align:center">Нет активных подключений</td></tr>';
            } else {
                stats.active_sessions.forEach(s => {
                    totalRx += s.bytes_recv; totalTx += s.bytes_sent;
                    tbody.innerHTML += `<tr class="status-online">
                        <td>${s.name}</td><td>${s.vpn_ip}</td>
                        <td>${formatBytes(s.bytes_recv)}</td><td>${formatBytes(s.bytes_sent)}</td>
                        <td>${s.connected_at}</td></tr>`;
                });
            }
            document.getElementById('total-rx').innerText = formatBytes(totalRx);
            document.getElementById('total-tx').innerText = formatBytes(totalTx);

            const usersRes = await fetch('/api/users');
            const users = await usersRes.json();
            const utbody = document.getElementById('users-tbody');
            utbody.innerHTML = '';
            users.forEach(u => {
                utbody.innerHTML += `<tr>
                    <td>${u.name}</td><td>${u.created_at}</td>
                    <td><button class="btn btn-danger" onclick="deleteUser('${u.name}')">Отозвать доступ</button></td></tr>`;
            });
        }

        async function addUser() {
            const name = document.getElementById('new-user-name').value;
            if (!name) return alert("Введите имя!");
            const res = await fetch('/api/users', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: name})
            });
            if (res.ok) {
                const data = await res.json();
                document.getElementById('token-text').innerText = data.token;
                document.getElementById('token-display').style.display = 'block';
                document.getElementById('new-user-name').value = '';
                loadData();
            } else {
                alert("Ошибка: Возможно имя уже существует.");
            }
        }

        async function deleteUser(name) {
            if (!confirm(`Отозвать доступ для ${name}?`)) return;
            await fetch(`/api/users/${name}`, { method: 'DELETE' });
            loadData();
        }

        // Обновление каждые 3 секунды
        loadData();
        setInterval(loadData, 3000);
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTML_DASHBOARD

@app.get("/api/stats")
async def get_stats():
    sessions = db.get_active_sessions()
    users = db.get_all_users()
    return {"active_sessions": [dict(s) for s in sessions], "total_users": len(users)}

@app.get("/api/users")
async def get_users():
    users = db.get_all_users()
    return [dict(u) for u in users]

@app.post("/api/users")
async def add_user(request: Request):
    data = await request.json()
    name = data.get("name")
    if not name: raise HTTPException(status_code=400, detail="Name is required")
    
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    
    if db.add_user(name, token_hash):
        return {"token": token}
    raise HTTPException(status_code=409, detail="User already exists")

@app.delete("/api/users/{name}")
async def delete_user(name: str):
    db.delete_user(name)
    return {"status": "deleted"}