#!/usr/bin/env python3
import sqlite3
import threading
import json
from pathlib import Path

DB_PATH = Path("vpn.db")
CONFIG_DIR = Path("vpn_config")
CONFIG_DIR.mkdir(exist_ok=True)
CLIENTS_KEYS_DB = CONFIG_DIR / "clients_keys.json"

class VpnDatabase:
    def __init__(self):
        self._local = threading.local()
        self._create_tables()

    def _get_conn(self):
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _create_tables(self):
        conn = self._get_conn()
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                token_hash TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                vpn_ip TEXT UNIQUE NOT NULL,
                bytes_sent INTEGER DEFAULT 0,
                bytes_recv INTEGER DEFAULT 0,
                connected_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_active TEXT DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        conn.commit()

    def add_user(self, name: str, token_hash: str):
        conn = self._get_conn()
        try:
            conn.execute("INSERT INTO users (name, token_hash) VALUES (?, ?)", (name, token_hash))
            conn.commit()
            
            db_data = {}
            if CLIENTS_KEYS_DB.exists():
                try: db_data = json.load(open(CLIENTS_KEYS_DB, 'r'))
                except: db_data = {}
            db_data[token_hash] = name
            with open(CLIENTS_KEYS_DB, 'w') as f:
                json.dump(db_data, f, indent=4)
            return True
        except sqlite3.IntegrityError:
            return False

    def get_user_by_hash(self, token_hash: str):
        conn = self._get_conn()
        row = conn.execute("SELECT name FROM users WHERE token_hash = ?", (token_hash,)).fetchone()
        return row['name'] if row else None

    def delete_user(self, name: str):
        conn = self._get_conn()
        row = conn.execute("SELECT token_hash FROM users WHERE name = ?", (name,)).fetchone()
        if row:
            token_hash = row['token_hash']
            conn.execute("DELETE FROM users WHERE name = ?", (name,))
            conn.commit()
            
            if CLIENTS_KEYS_DB.exists():
                try:
                    db_data = json.load(open(CLIENTS_KEYS_DB, 'r'))
                    if token_hash in db_data: del db_data[token_hash]
                    with open(CLIENTS_KEYS_DB, 'w') as f: json.dump(db_data, f, indent=4)
                except: pass

    def get_all_users(self):
        conn = self._get_conn()
        return conn.execute("SELECT name, created_at FROM users ORDER BY created_at DESC").fetchall()

    def create_session(self, name: str, vpn_ip: str):
        conn = self._get_conn()
        conn.execute("INSERT OR REPLACE INTO sessions (name, vpn_ip, bytes_sent, bytes_recv, connected_at) VALUES (?, ?, 0, 0, CURRENT_TIMESTAMP)", (name, vpn_ip))
        conn.commit()

    def update_stats(self, vpn_ip: str, bytes_sent: int, bytes_recv: int):
        conn = self._get_conn()
        conn.execute("UPDATE sessions SET bytes_sent = bytes_sent + ?, bytes_recv = bytes_recv + ?, last_active = CURRENT_TIMESTAMP WHERE vpn_ip = ?", 
                     (bytes_sent, bytes_recv, vpn_ip))
        conn.commit()

    def remove_session(self, vpn_ip: str):
        conn = self._get_conn()
        conn.execute("DELETE FROM sessions WHERE vpn_ip = ?", (vpn_ip,))
        conn.commit()

    def get_active_sessions(self):
        conn = self._get_conn()
        return conn.execute("SELECT name, vpn_ip, bytes_sent, bytes_recv, connected_at FROM sessions ORDER BY connected_at DESC").fetchall()