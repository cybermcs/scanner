import asyncio
import socket
import struct
import json
import sqlite3
import os
from datetime import datetime

# ========= CONFIG =========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SENT_FILE = os.path.join(BASE_DIR, "ressources", "sent_servers.txt")
DATABASE_FILE = os.path.join(BASE_DIR, "ressources", "servers.db")

CONCURRENCY = 500
TIMEOUT = 3


# ========= VARINT =========
def encode_varint(value):
    out = b""
    while True:
        byte = value & 0x7F
        value >>= 7
        out += struct.pack("B", byte | (0x80 if value else 0))
        if not value:
            break
    return out


def parse_motd(description):
    if isinstance(description, str):
        return description
    if isinstance(description, dict):
        text = description.get("text", "")
        if "extra" in description and isinstance(description["extra"], list):
            for part in description["extra"]:
                if isinstance(part, dict):
                    text += part.get("text", "")
        return text.strip()
    return ""


# ========= DATABASE =========
def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            port INTEGER NOT NULL,
            motd TEXT,
            version TEXT,
            players_online INTEGER,
            players_max INTEGER,
            host TEXT,
            scanned_at TIMESTAMP,
            UNIQUE(ip, port)
        )
    """)
    conn.commit()
    conn.close()


def get_existing_servers():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT ip || ':' || port FROM servers")
    existing = set(row[0] for row in cursor.fetchall())
    conn.close()
    return existing


def save_server(ip, port, data):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    motd = parse_motd(data.get("description", ""))
    version = data.get("version", {}).get("name", "Unknown")
    players_online = data.get("players", {}).get("online", 0)
    players_max = data.get("players", {}).get("max", 0)
    try:
        host = socket.gethostbyaddr(ip)[0]
    except:
        host = ""
    cursor.execute("""
        INSERT OR REPLACE INTO servers
        (ip, port, motd, version, players_online, players_max, host, scanned_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (ip, port, motd, version, players_online, players_max, host, datetime.now()))
    conn.commit()
    conn.close()


# ========= ASYNC PING =========
async def ping(ip, port):
    reader = writer = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=TIMEOUT)

        handshake = (
            encode_varint(0) +
            encode_varint(754) +
            encode_varint(len(ip)) + ip.encode() +
            struct.pack(">H", port) +
            encode_varint(1)
        )

        writer.write(encode_varint(len(handshake)) + handshake)
        writer.write(b"\x01\x00")
        await writer.drain()

        # read length varint (max 5 bytes)
        length_bytes = await reader.read(5)
        if not length_bytes:
            return None

        # read rest of packet
        data = await reader.read(2048)  # nur max 2 KB
        if not data:
            return None

        try:
            return json.loads(data.decode(errors="ignore"))
        except:
            return None

    except (asyncio.TimeoutError, ConnectionResetError, OSError):
        return None
    finally:
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass


async def main():
    init_db()
    all_servers = []
    if os.path.exists(SENT_FILE):
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if ":" in s:
                    all_servers.append(s)

    existing = get_existing_servers()
    new_servers = [s for s in all_servers if s not in existing]

    print(f"New servers to scan: {len(new_servers)}")
    if not new_servers:
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    results = []

    async def scan(ip, port):
        async with sem:
            data = await ping(ip, port)
            if data:
                save_server(ip, port, data)
                print(f"[+] Saved {ip}:{port}")

    tasks = []
    for entry in new_servers:
        ip, port = entry.split(":")
        tasks.append(scan(ip, int(port)))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
