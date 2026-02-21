import asyncio, random, socket, struct, json, aiohttp, os, sys, time, sqlite3, subprocess
from colorama import Fore, Style, init
import config.config as config
import threading
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from ressources.instance_manager import get_instance_manager, StatsMessage
from datetime import datetime


# ======== LOG FUNCTIONS ========
def log_print(message: str, tag: str = None):
    """Print a message to the log text field"""
    global logs_text
    if not logs_text:
        return
    try:
        if not logs_text.winfo_exists():
            return
        logs_text.insert("end", message + "\n", tag)
        logs_text.see("end")
    except Exception:
        pass

try:
    import tkinter as tk
    from tkinter import ttk
except Exception:
    tk = None
    ttk = None

executor = ThreadPoolExecutor(max_workers=max(50, config.CONCURRENCY * 2))

http_session: aiohttp.ClientSession | None = None

last_title_update = 0
last_title_scan_count = 0

TITLE_MIN_SECONDS = getattr(config, 'TITLE_MIN_SECONDS', 0.5)
TITLE_SCAN_STEP = getattr(config, 'TITLE_SCAN_STEP', 10)

# GUI references
gui_root = None
scan_log_text = None
logs_text = None
stats_labels = {}
recent_box = None

# Thread-safe GUI message queue
gui_message_queue: Queue = Queue()
gui_queue_processing = False

# Scanner instance control
active_scanners = 1
scanner_instances = []
stop_event = asyncio.Event()

# Multi-run control
target_runs = 0
current_run = 0
runs_completed = 0

# Instance management
instance_mgr = get_instance_manager()
is_worker_mode = False
worker_stats_lock = threading.Lock()
worker_local_stats = {
    "scanned": 0,
    "found": 0,
    "with_players": 0,
    "sent_count": 0
}

def on_worker_stats_received(message):
    pass

def on_worker_disconnect(worker_id):
    gui_print(f"[MASTER] Worker {worker_id[:8]}... disconnected", "error")

def on_server_broadcast(server_key: str):
    global sent_set
    sent_set.add(server_key)
    gui_print(f"[SYNC] Received server update from master: {server_key}", "webhook")


init(autoreset=True)

# ========= FARBEN =========
SCAN = Fore.YELLOW
NOSRV = Fore.RED
EMPTY = Fore.GREEN
ONLINE = Fore.GREEN
WEBHOOK = Fore.CYAN
ERROR = Fore.MAGENTA

Pink = Fore.MAGENTA

RAINBOW = [Fore.RED, Fore.YELLOW, Fore.GREEN, Fore.CYAN, Fore.BLUE, Fore.MAGENTA]
PINK = [Fore.RED, Fore.LIGHTMAGENTA_EX, Fore.MAGENTA, Fore.LIGHTRED_EX]
PINK_GRAD = [Fore.LIGHTMAGENTA_EX, Fore.MAGENTA, Fore.RED]

CYAN = "#00ffea"
RED = "#ff0055"
YELLOW = "#ffff00"
GREEN = "#00ff99"
NEON = "#ff4df2"


# ========= SENT PERSISTENCE =========
SENT_FILE = "ressources//sent_servers.txt"
sent_set: set = set()
sent_lock = asyncio.Lock()

def load_sent():
    global sent_set
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                k = line.strip()
                if k:
                    sent_set.add(k)
    except FileNotFoundError:
        open(SENT_FILE, "a", encoding="utf-8").close()

def _append_sent_file(key: str):
    try:
        with open(SENT_FILE, "a", encoding="utf-8") as f:
            f.write(key + "\n")
    except Exception:
        pass

async def mark_sent(key: str) -> bool:
    global sent_set

    if is_worker_mode and instance_mgr.master_socket:
        already_sent = await asyncio.get_running_loop().run_in_executor(
            None, instance_mgr.check_server_sent, key
        )
        if already_sent:
            async with sent_lock:
                sent_set.add(key)
            return False

        success = await asyncio.get_running_loop().run_in_executor(
            None, instance_mgr.mark_server_sent, key
        )
        if not success:
            async with sent_lock:
                sent_set.add(key)
            return False

    async with sent_lock:
        if key in sent_set:
            return False
        sent_set.add(key)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _append_sent_file, key)
    return True


load_sent()

# ========= DATABASE FUNCTIONS =========
DATABASE_FILE = "ressources//servers.db"

def init_db():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL,
                port INTEGER NOT NULL,
                motd TEXT,
                version TEXT,
                players_online INTEGER,
                players_max INTEGER,
                host TEXT,
                bild TEXT,
                scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(ip, port)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ip_port ON servers(ip, port)')
        conn.commit()
        conn.close()
    except Exception as e:
        gui_print(f"[DB] Error initializing database: {e}")

def get_servers_from_db(search_query=""):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if search_query:
            try:
                player_count = int(search_query)
                query = "SELECT * FROM servers WHERE players_online = ? ORDER BY scanned_at DESC"
                cursor.execute(query, (player_count,))
            except ValueError:
                query = """
                    SELECT * FROM servers
                    WHERE ip LIKE ? OR motd LIKE ? OR version LIKE ? OR host LIKE ?
                    ORDER BY scanned_at DESC
                """
                search_pattern = f"%{search_query}%"
                cursor.execute(query, (search_pattern, search_pattern, search_pattern, search_pattern))
        else:
            cursor.execute("SELECT * FROM servers ORDER BY scanned_at DESC LIMIT 1000000000000")

        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results
    except Exception as e:
        gui_print(f"[DB] Error getting servers: {e}")
        return []

def get_server_count():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM servers")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except:
        return 0

def update_server(ip, port, motd, version, players_online, players_max, host="", bild=""):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO servers
            (ip, port, motd, version, players_online, players_max, host, bild, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ''', (ip, port, motd, version, players_online, players_max, host, bild))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[DB] Error updating server: {e}")
        return False

init_db()

# ========= YOURSERVERS HELPER FUNCTIONS =========
servers_tree = None
servers_search_var = None
server_count_label = None

def run_server_checker():
    import subprocess
    import threading

    def run_checker():
        try:
            gui_print("[YourSERVERS] Starting server checker...", "scan")
            result = subprocess.run(
                [sys.executable, "server_checker.py"],
                capture_output=True,
                text=True,
                timeout=3600
            )
            if result.returncode == 0:
                gui_print("[YourSERVERS] Server checker completed successfully!", "online")
            else:
                gui_print(f"[YourSERVERS] Server checker error: {result.stderr}", "error")
            try:
                refresh_servers_list()
            except Exception as e:
                gui_print(f"[YourSERVERS] Error refreshing list: {e}", "error")
        except subprocess.TimeoutExpired:
            gui_print("[YourSERVERS] Server checker timed out!", "error")
        except Exception as e:
            gui_print(f"[YourSERVERS] Server checker error: {e}", "error")

    try:
        checker_thread = threading.Thread(target=run_checker, daemon=True)
        checker_thread.start()
    except Exception as e:
        gui_print(f"[YourSERVERS] Failed to start checker thread: {e}", "error")

def refresh_servers_list():
    global servers_tree, servers_search_var, server_count_label

    if servers_tree is None:
        return

    try:
        for item in servers_tree.get_children():
            servers_tree.delete(item)

        search_query = servers_search_var.get() if servers_search_var else ""
        if search_query == "Search servers...":
            search_query = ""

        servers = get_servers_from_db(search_query)

        for server in servers:
            ip_port = f"{server['ip']}:{server['port']}"
            motd = server.get('motd', '') or ''
            if len(motd) > 40:
                motd = motd[:37] + "..."
            version = server.get('version', '') or 'Unknown'
            players = f"{server.get('players_online', 0)}/{server.get('players_max', 0)}"
            scanned_at = server.get('scanned_at', '') or ''

            servers_tree.insert('', 'end', values=(ip_port, motd, version, players, scanned_at))

        if server_count_label:
            server_count_label.config(text=f"Servers: {len(servers)}")

        gui_print(f"[YourSERVERS] Loaded {len(servers)} servers from database", "scan")

    except Exception as e:
        gui_print(f"[YourSERVERS] Error refreshing servers list: {e}", "error")


def ping_single_server(ip, port):
    try:
        s = socket.socket()
        s.settimeout(config.TIMEOUT)
        s.connect((ip, port))

        handshake = (
            encode_varint(0) +
            encode_varint(754) +
            encode_varint(len(ip)) + ip.encode() +
            struct.pack(">H", port) +
            encode_varint(1)
        )

        s.sendall(encode_varint(len(handshake)) + handshake)
        s.sendall(b"\x01\x00")

        decode_varint(s)
        decode_varint(s)
        length = decode_varint(s)

        if not length:
            s.close()
            return None

        data = s.recv(length)
        s.close()

        return json.loads(data.decode())
    except Exception:
        return None


def open_server_detail(server_data):
    global gui_root

    BG = "#000000"
    CARD = "#050505"
    PINK = "#ff00aa"
    PURPLE = "#8a2be2"
    NEON = "#ff4df2"
    CYAN = "#00ffea"
    GREEN = "#00ff99"
    RED = "#ff0055"
    YELLOW = "#ffff00"

    popup = tk.Toplevel(gui_root)
    ip = server_data.get('ip', '')
    port = server_data.get('port', 25565)
    popup.title(f"Server Details - {ip}:{port}")
    popup.geometry("600x500")
    popup.configure(bg=BG)
    popup.resizable(False, False)
    popup.transient(gui_root)
    popup.grab_set()

    title_frame = tk.Frame(popup, bg=CARD, height=40)
    title_frame.pack(fill="x")
    title_frame.pack_propagate(False)

    title_label = tk.Label(
        title_frame, text="🖥️ SERVER DETAILS", bg=CARD, fg=PINK, font=("Consolas", 14, "bold")
    )
    title_label.pack(side="left", padx=15, pady=8)

    close_btn = tk.Label(title_frame, text=" ✕ ", bg=CARD, fg=PINK, font=("Segoe UI", 12, "bold"), cursor="hand2")
    close_btn.pack(side="right", padx=10)
    close_btn.bind("<Button-1>", lambda e: popup.destroy())

    content = tk.Frame(popup, bg=BG)
    content.pack(fill="both", expand=True, padx=20, pady=20)
    log_print(f"[YourSERVERS] Opened details for {ip}:{port}", "scan")

    ip_port_label = tk.Label(content, text=f"{ip}:{port}", bg=BG, fg=CYAN, font=("Consolas", 20, "bold"))
    ip_port_label.pack(pady=(0, 15))

    status_frame = tk.Frame(content, bg=CARD, highlightbackground=PURPLE, highlightthickness=2)
    status_frame.pack(fill="x", pady=(0, 15))

    status_label = tk.Label(status_frame, text="⚫ OFFLINE", bg=CARD, fg=RED, font=("Consolas", 14, "bold"))
    status_label.pack(pady=15)

    info_frame = tk.Frame(content, bg=CARD, highlightbackground=PURPLE, highlightthickness=2)
    info_frame.pack(fill="both", expand=True)

    def create_info_row(parent, label_text, value_text, row):
        tk.Label(parent, text=label_text, bg=CARD, fg=PINK, font=("Consolas", 10, "bold"), anchor="w").grid(row=row, column=0, sticky="w", padx=15, pady=10)
        value = tk.Label(parent, text=value_text, bg=CARD, fg=CYAN, font=("Consolas", 10), anchor="w")
        value.grid(row=row, column=1, sticky="w", padx=15, pady=10)
        return value

    tk.Label(info_frame, text="MOTD", bg=CARD, fg=PINK, font=("Consolas", 10, "bold"), anchor="w").grid(row=0, column=0, sticky="w", padx=15, pady=(15, 5))
    motd_value = tk.Label(info_frame, text=server_data.get('motd', 'N/A') or 'N/A', bg=CARD, fg=CYAN, font=("Consolas", 10), anchor="w", wraplength=500)
    motd_value.grid(row=0, column=1, sticky="w", padx=15, pady=(15, 5))

    version_value = create_info_row(info_frame, "Version", server_data.get('version', 'Unknown') or 'Unknown', 1)
    players_online = server_data.get('players_online', 0)
    players_max = server_data.get('players_max', 0)
    players_value = create_info_row(info_frame, "Players", f"{players_online} / {players_max}", 2)
    host_value = create_info_row(info_frame, "Host", server_data.get('host', 'N/A') or 'N/A', 3)
    scanned_value = create_info_row(info_frame, "Last Scanned", server_data.get('scanned_at', 'N/A') or 'N/A', 4)
    id_value = create_info_row(info_frame, "Server ID", str(server_data.get('id', 'N/A')), 5)

    btn_frame = tk.Frame(content, bg=BG)
    btn_frame.pack(fill="x", pady=(15, 0))

    reinitalize_btn = tk.Button(
        btn_frame, text="🔄 ReInitialize", bg=PURPLE, fg="#ffffff",
        font=("Consolas", 12, "bold"), bd=0, padx=20, pady=10, cursor="hand2",
        activebackground=PINK, activeforeground="#ffffff"
    )
    reinitalize_btn.pack(side="left", padx=(0, 10))

    tk.Button(
        btn_frame, text="✕ Close", command=popup.destroy,
        bg=CARD, fg=PINK, font=("Consolas", 12, "bold"), bd=2,
        highlightbackground=PURPLE, highlightthickness=2,
        padx=20, pady=10, cursor="hand2",
        activebackground=PURPLE, activeforeground="#ffffff"
    ).pack(side="right")

    def reinitalize_server():
        reinitalize_btn.config(text="⏳ Checking...", state="disabled")
        status_label.config(text="⏳ CHECKING...", fg=YELLOW)
        popup.update()

        result = ping_single_server(ip, port)

        if result:
            status_label.config(text="🟢 ONLINE", fg=GREEN)
            motd = result.get("description", "")
            if isinstance(motd, dict):
                motd = motd.get("text", "") or str(motd)
            motd_value.config(text=motd if motd else "N/A")
            version = result.get("version", {}).get("name", "Unknown")
            version_value.config(text=version)
            p_online = result.get("players", {}).get("online", 0)
            p_max = result.get("players", {}).get("max", 0)
            players_value.config(text=f"{p_online} / {p_max}")
            update_server(ip, port, motd, version, p_online, p_max, server_data.get('host', ''), '')
            gui_print(f"[YourSERVERS] Updated server {ip}:{port} - {p_online}/{p_max} players", "online")
        else:
            status_label.config(text="🔴 OFFLINE", fg=RED)
            players_value.config(text="0 / 0")

        reinitalize_btn.config(text="🔄 ReInitialize", state="normal")

    reinitalize_btn.config(command=reinitalize_server)

    def try_ping_on_open():
        result = ping_single_server(ip, port)
        if result:
            status_label.config(text="🟢 ONLINE", fg=GREEN)
        else:
            status_label.config(text="🔴 OFFLINE", fg=RED)

    popup.after(500, try_ping_on_open)


# ========= GUI OUTPUT FUNCTIONS =========
def gui_print(message: str, tag: str = None):
    global scan_log_text

    if not scan_log_text:
        return

    try:
        if not scan_log_text.winfo_exists():
            return

        scan_log_text.insert("end", message + "\n", tag)
        scan_log_text.see("end")

        line_count = int(scan_log_text.index("end-1c").split(".")[0])
        if line_count > 1200:
            scan_log_text.delete("1.0", "300.0")

    except Exception:
        pass


def gui_clear():
    global scan_log_text
    if scan_log_text:
        try:
            scan_log_text.delete('1.0', tk.END)
        except:
            pass

def gui_drain_message_queue():
    try:
        while not gui_message_queue.empty():
            try:
                message, tag = gui_message_queue.get_nowait()
                gui_print(message, tag)
            except Exception:
                break
    except Exception:
        pass
    try:
        if gui_root and gui_root.winfo_exists():
            gui_root.after(100, gui_drain_message_queue)
    except Exception:
        pass

def gui_update_stats():
    global stats_labels, recent_box, gui_root, active_scanners, target_runs, current_run, runs_completed
    if not gui_root:
        return

    try:
        if not gui_root.winfo_exists():
            return

        if instance_mgr.is_master:
            all_stats = instance_mgr.get_all_stats()
            total_scanned = scanned + all_stats["total_scanned"]
            total_found = found + all_stats["total_found"]
            total_with_players = with_players + all_stats["total_with_players"]
            total_sent = sent_count + all_stats["total_sent"]
            worker_count = all_stats["active_workers"]
        else:
            total_scanned = scanned
            total_found = found
            total_with_players = with_players
            total_sent = sent_count
            worker_count = 0

        if "Scanned" in stats_labels and stats_labels["Scanned"].winfo_exists():
            stats_labels["Scanned"].config(text=str(total_scanned))
        if "Found" in stats_labels and stats_labels["Found"].winfo_exists():
            stats_labels["Found"].config(text=str(total_found))
        if "With Players" in stats_labels and stats_labels["With Players"].winfo_exists():
            stats_labels["With Players"].config(text=str(total_with_players))

        rate = compute_rate_per_hour(60)
        if "Server scanner per hour" in stats_labels and stats_labels["Server scanner per hour"].winfo_exists():
            stats_labels["Server scanner per hour"].config(text=f"{rate:.0f}")

        if "Webhooks Sent" in stats_labels and stats_labels["Webhooks Sent"].winfo_exists():
            stats_labels["Webhooks Sent"].config(text=str(total_sent))

        if "Active Scanners" in stats_labels and stats_labels["Active Scanners"].winfo_exists():
            if instance_mgr.is_master:
                stats_labels["Active Scanners"].config(text=str(worker_count + 1))
            else:
                stats_labels["Active Scanners"].config(text="1")

        if "Run Progress" in stats_labels and stats_labels["Run Progress"].winfo_exists():
            if target_runs >= 2:
                progress_text = f"{runs_completed}/{target_runs}"
                if current_run > runs_completed and current_run <= target_runs:
                    progress_text = f"{current_run}/{target_runs} (running)"
                stats_labels["Run Progress"].config(text=progress_text)
            else:
                stats_labels["Run Progress"].config(text="-")

        if recent_box and recent_box.winfo_exists():
            recent_box.delete(0, tk.END)
            with recent_found_lock:
                for ip in list(recent_found):
                    recent_box.insert(tk.END, ip)
    except Exception:
        pass

    try:
        if gui_root and gui_root.winfo_exists():
            gui_root.after(500, gui_update_stats)
    except:
        pass


def gui_update_advanced_stats():
    global advanced_stats_labels, scan_graph_canvas, graph_bars, gui_root
    global peak_scans_per_minute, peak_found_per_minute

    if not gui_root:
        return

    try:
        if not gui_root.winfo_exists():
            return
    except:
        return

    if not advanced_stats_labels:
        try:
            if gui_root.winfo_exists():
                gui_root.after(1000, gui_update_advanced_stats)
        except:
            pass
        return

    try:
        scans_per_min = compute_scans_per_minute(60)
        found_per_min = compute_found_per_minute(60)
        current_rate = compute_scans_per_minute(10) / 10

        with peak_stats_lock:
            if scans_per_min > peak_scans_per_minute:
                peak_scans_per_minute = scans_per_min

        if instance_mgr.is_master:
            all_stats = instance_mgr.get_all_stats()
            total_scans_per_min = scans_per_min + all_stats.get("total_scans_per_minute", 0)
            total_found_per_min = found_per_min + all_stats.get("total_found_per_minute", 0)
            max_peak_scans = max(peak_scans_per_minute, all_stats.get("max_peak_scans_per_minute", 0))
        else:
            total_scans_per_min = scans_per_min
            total_found_per_min = found_per_min
            max_peak_scans = peak_scans_per_minute

        if "scans_per_min" in advanced_stats_labels and advanced_stats_labels["scans_per_min"].winfo_exists():
            advanced_stats_labels["scans_per_min"].config(text=f"{total_scans_per_min:.1f}")
        if "found_per_min" in advanced_stats_labels and advanced_stats_labels["found_per_min"].winfo_exists():
            advanced_stats_labels["found_per_min"].config(text=f"{total_found_per_min:.1f}")
        if "current_rate" in advanced_stats_labels and advanced_stats_labels["current_rate"].winfo_exists():
            advanced_stats_labels["current_rate"].config(text=f"{current_rate:.1f}/s")
        if "peak_scans" in advanced_stats_labels and advanced_stats_labels["peak_scans"].winfo_exists():
            advanced_stats_labels["peak_scans"].config(text=f"{max_peak_scans:.1f}")

        now = time.time()
        with scan_times_lock:
            one_second_ago = now - 1
            scans_last_second = sum(1 for ts in scan_times if ts >= one_second_ago)

        with scan_history_lock:
            scan_history.append((now, scans_last_second))
            while len(scan_history) > 10:
                scan_history.popleft()

        global last_graph_update
        if now - last_graph_update >= 10 and scan_graph_canvas:
            try:
                scan_graph_canvas.delete("bar")

                with scan_history_lock:
                    data = list(scan_history)

                if not data:
                    return

                max_val = max((count for _, count in data), default=1)
                if max_val < 1:
                    max_val = 1

                bar_width = 30
                spacing = 35
                start_x = 55

                for i, (timestamp, count) in enumerate(data):
                    bar_height = (count / max_val) * 200 if max_val > 0 else 0
                    if bar_height < 2 and count > 0:
                        bar_height = 2

                    x = start_x + i * spacing
                    y_bottom = 230
                    y_top = y_bottom - bar_height

                    if count / max_val > 0.7:
                        color = "#ff00aa"
                    elif count / max_val > 0.4:
                        color = "#8a2be2"
                    else:
                        color = "#00ffea"

                    scan_graph_canvas.create_rectangle(
                        x - bar_width/2, y_top, x + bar_width/2, y_bottom,
                        fill=color, outline="", tags="bar"
                    )

                    if bar_height > 15:
                        scan_graph_canvas.create_text(
                            x, y_top - 8, text=str(count), fill="#ffffff",
                            font=("Consolas", 8, "bold"), tags="bar"
                        )

                scan_graph_canvas.delete("max_label")
                scan_graph_canvas.create_text(
                    20, 20, text=f"{int(max_val)}", fill="#666666",
                    font=("Consolas", 8), tags="max_label"
                )

                last_graph_update = now
            except Exception:
                pass

    except Exception:
        pass

    try:
        if gui_root and gui_root.winfo_exists():
            gui_root.after(1000, gui_update_advanced_stats)
    except:
        pass


# ========= COUNTER =========
scanned = 0
found = 0
with_players = 0
sent_count = 0
counter_lock = threading.Lock()

scan_times: deque = deque(maxlen=1000)
scan_times_lock = threading.Lock()
recent_found: deque = deque(maxlen=20)
recent_found_lock = threading.Lock()

# ========= TITLE =========
def set_title():
    global last_title_update, last_title_scan_count
    now = time.time()
    time_ok = (now - last_title_update) >= TITLE_MIN_SECONDS
    scans_ok = (scanned - last_title_scan_count) >= TITLE_SCAN_STEP
    if not (time_ok or scans_ok):
        return
    last_title_update = now
    last_title_scan_count = scanned

# ========= RATE CALCULATION =========
def compute_rate_per_hour(window_seconds: int = 60) -> float:
    now = time.time()
    cutoff = now - window_seconds
    with scan_times_lock:
        count = sum(1 for ts in reversed(scan_times) if ts >= cutoff)
    if window_seconds == 0:
        return 0.0
    return (count / window_seconds) * 3600.0

def compute_scans_per_minute(window_seconds: int = 60) -> float:
    now = time.time()
    cutoff = now - window_seconds
    with scan_times_lock:
        count = sum(1 for ts in reversed(scan_times) if ts >= cutoff)
    if window_seconds == 0:
        return 0.0
    return (count / window_seconds) * 60.0

def compute_found_per_minute(window_seconds: int = 60) -> float:
    global found_times, found_times_lock
    try:
        found_times
    except NameError:
        found_times = deque(maxlen=10000)
        found_times_lock = threading.Lock()

    now = time.time()
    cutoff = now - window_seconds
    with found_times_lock:
        count = sum(1 for ts in reversed(found_times) if ts >= cutoff)
    if window_seconds == 0:
        return 0.0
    return (count / window_seconds) * 60.0

found_times: deque = deque(maxlen=1000)
found_times_lock = threading.Lock()

# ========= ADVANCED STATS TRACKING =========
scan_history: deque = deque(maxlen=10)
scan_history_lock = threading.Lock()

peak_scans_per_minute = 0.0
peak_found_per_minute = 0.0
peak_stats_lock = threading.Lock()

advanced_stats_labels = {}
scan_graph_canvas = None
graph_data = [0] * 10
last_graph_update = 0


# ========= CONFIG FUNCTIONS =========
def save_config_settings(webhook_url, port, timeout, concurrency, web_host, web_port):
    try:
        config_path = os.path.join(os.path.dirname(__file__), "config", "config.py")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(f'''WEBHOOK_URL = "{webhook_url}"
PORT = {port}
TIMEOUT = {timeout}
CONCURRENCY = {concurrency}
WEB_HOST = "{web_host}"
WEB_PORT = {web_port}
''')
        return True
    except Exception as e:
        gui_print(f"[ERROR] Failed to save config: {e}")
        return False

def load_ascii_art():
    try:
        with open("ascii/ascii_art.txt", "r", encoding="utf-8") as f:
            return f.read()
    except:
        return """
    🌹 CYBER MCS SCANNER 🌹

    Version 2.0
    Created with 💜
        """


# ========= WEBHOOK (IMPROVED) =========

def _strip_motd(motd) -> str:
    """Return plain-text MOTD, stripping § color codes."""
    if isinstance(motd, dict):
        motd = motd.get("text", "") or str(motd)
    motd = str(motd)
    motd = re.sub(r'§.', '', motd)
    motd = motd.replace("\n", " ").strip()
    return motd[:256] or "—"


def _thumb_url(data: dict) -> str | None:
    """Extract base64 favicon from ping data and return a data-URI, or None."""
    favicon = data.get("favicon", "")
    if favicon and favicon.startswith("data:image/png;base64,"):
        return favicon
    return None


def build_active_embed(ip: str, port: int, players: int, maxp: int,
                       version: str, motd, data: dict,
                       cracked_str: str, whitelist_str: str,
                       disconnect_str: str, join_msg: str,
                       advisory_str: str | None = None) -> dict:
    """Embed for servers WITH active players."""
    motd_clean = _strip_motd(motd)
    thumb = _thumb_url(data)
    color = 0x00FF66 if players >= 3 else 0xFFD700

    embed = {
        "title": "🚨  ACTIVE PLAYERS DETECTED",
        "description": (
            f"```\n{ip}:{port}\n```\n"
            f"**{players}/{maxp} players online**"
        ),
        "color": color,
        "fields": [
            {"name": "🕹️  Version",  "value": f"`{version}`",             "inline": True},
            {"name": "👥  Players",  "value": f"**{players}** / {maxp}",  "inline": True},
            {"name": "📋  MOTD",     "value": f"```\n{motd_clean}\n```",  "inline": False},
            {"name": "🔓  Cracked",  "value": cracked_str,                "inline": True},
            {"name": "📋  Whitelist","value": whitelist_str,               "inline": True},
        ],
        "footer": {"text": f"🌹 Cyber MCS Scanner  •  Port {port}"},
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    if disconnect_str and disconnect_str != "N/A":
        embed["fields"].append({
            "name": "⛔  Disconnect Message",
            "value": f"```\n{disconnect_str[:200]}\n```",
            "inline": False
        })

    if advisory_str:
        embed["fields"].append({
            "name": "⚠️  Advisory",
            "value": advisory_str[:400],
            "inline": False
        })

    if thumb:
        embed["thumbnail"] = {"url": thumb}

    return embed


def build_empty_embed(ip: str, port: int, maxp: int,
                      version: str, motd, data: dict,
                      cracked_str: str, whitelist_str: str,
                      disconnect_str: str, join_msg: str,
                      advisory_str: str | None = None) -> dict:
    """Embed for servers with 0 players."""
    motd_clean = _strip_motd(motd)
    thumb = _thumb_url(data)

    embed = {
        "title": "💤  Server Found — No Players",
        "description": f"```\n{ip}:{port}\n```",
        "color": 0xFF8C00,
        "fields": [
            {"name": "🕹️  Version",  "value": f"`{version}`",            "inline": True},
            {"name": "👥  Slots",    "value": f"0 / {maxp}",             "inline": True},
            {"name": "📋  MOTD",     "value": f"```\n{motd_clean}\n```", "inline": False},
            {"name": "🔓  Cracked",  "value": cracked_str,               "inline": True},
            {"name": "📋  Whitelist","value": whitelist_str,              "inline": True},
        ],
        "footer": {"text": f"🌹 Cyber MCS Scanner  •  Port {port}"},
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    if disconnect_str and disconnect_str != "N/A":
        embed["fields"].append({
            "name": "⛔  Disconnect Message",
            "value": f"```\n{disconnect_str[:200]}\n```",
            "inline": False
        })

    if advisory_str:
        embed["fields"].append({
            "name": "⚠️  Advisory",
            "value": advisory_str[:400],
            "inline": False
        })

    if thumb:
        embed["thumbnail"] = {"url": thumb}

    return embed


async def webhook(msg):
    """Send a Discord webhook with retry logic and rate-limit handling."""
    global http_session

    if not config.WEBHOOK_URL or config.WEBHOOK_URL.strip() == "":
        gui_print("[WEBHOOK] ERROR: WEBHOOK_URL is empty in config!", "error")
        return

    if http_session is None:
        http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=getattr(config, 'WEBHOOK_TIMEOUT', 10))
        )

    payload = {}
    if isinstance(msg, dict):
        payload["embeds"] = [msg]
        payload["username"] = "🌹 MCS Scanner"
    else:
        payload["content"] = msg
        payload["username"] = "🌹 MCS Scanner"

    for attempt in range(3):
        try:
            async with http_session.post(config.WEBHOOK_URL, json=payload) as r:
                if r.status == 429:
                    retry_after = 1.0
                    try:
                        retry_after = float((await r.json()).get("retry_after", 1))
                    except Exception:
                        pass
                    gui_print(f"[WEBHOOK] Rate limited, retrying in {retry_after:.1f}s...", "error")
                    await asyncio.sleep(retry_after)
                    continue
                if r.status not in (200, 204):
                    body = await r.text()
                    gui_print(f"[WEBHOOK ERROR] {r.status}: {body[:120]}", "error")
                    return
                gui_print(f"[WEBHOOK] ✅ Sent successfully", "webhook")
                return
        except Exception as e:
            gui_print(f"[WEBHOOK FAIL] attempt {attempt+1}/3: {e}", "error")
            await asyncio.sleep(1.5)


# ========= MAIN GUI WINDOW =========
def run_main_gui():
    global gui_root, scan_log_text, stats_labels, recent_box

    if tk is None:
        return

    gui_root = tk.Tk()
    gui_root.overrideredirect(True)
    gui_root.geometry("1000x700")
    gui_root.configure(bg="#000000")

    BG = "#000000"
    CARD = "#050505"
    PINK = "#ff00aa"
    PURPLE = "#8a2be2"
    NEON = "#ff4df2"

    # ================= TITLE BAR =================
    title_bar = tk.Frame(gui_root, bg="#000000", height=40)
    title_bar.pack(fill="x")
    title_bar.pack_propagate(False)

    title_label = tk.Label(
        title_bar, text="🌹 CYBER MCS SCANNER 🌹",
        bg="#000000", fg=PINK, font=("Segoe UI", 14, "bold")
    )
    title_label.pack(side="left", padx=15)

    glow_colors = ["#ff0080", "#ff00ff", "#ff4df2", "#ff1493"]
    glow_state = [0]

    def animate_rose():
        title_label.config(fg=glow_colors[glow_state[0] % len(glow_colors)])
        glow_state[0] += 1
        gui_root.after(400, animate_rose)

    animate_rose()

    connect_frame = tk.Frame(title_bar, bg="#070707", highlightbackground="#8a2be2", highlightthickness=1)
    connect_frame.pack(side="right", padx=20, pady=8)

    tk.Label(connect_frame, text=" CONNECT: ", bg="#070707", fg="#a020f0", font=("Consolas", 10, "bold")).pack(side="left", padx=(8, 4))

    connect_entry = tk.Entry(
        connect_frame, bg="#000000", fg="#ff00aa", insertbackground="#ff00aa",
        bd=0, font=("Consolas", 10), width=18
    )
    connect_entry.pack(side="left", padx=(0, 8), pady=4)

    def on_focus_in(e):
        connect_frame.config(highlightbackground="#ff00aa", highlightthickness=2)

    def on_focus_out(e):
        connect_frame.config(highlightbackground="#8a2be2", highlightthickness=1)

    connect_entry.bind("<FocusIn>", on_focus_in)
    connect_entry.bind("<FocusOut>", on_focus_out)

    def on_enter(e):
        global target_runs, current_run, runs_completed
        value = connect_entry.get().strip().lower()
        if value.startswith("run "):
            try:
                num_runs = int(value.split()[1])
                if 2 <= num_runs <= 10:
                    target_runs = num_runs
                    current_run = 1
                    runs_completed = 0
                    gui_print(f"\n[CONFIG] Multi-run mode activated: {target_runs} runs", "scan")
                    gui_print(f"[CONFIG] Starting run 1/{target_runs}...\n", "scan")
                    connect_entry.delete(0, tk.END)
                else:
                    gui_print(f"[ERROR] Run count must be between 2 and 10 (got {num_runs})", "error")
            except (ValueError, IndexError):
                gui_print(f"[ERROR] Invalid command. Use: run 2-10", "error")
        else:
            print("CONNECT VALUE:", value)

    connect_entry.bind("<Return>", on_enter)

    close_btn = tk.Label(title_bar, text=" ✕ ", bg="#000000", fg=PINK, font=("Segoe UI", 12, "bold"), cursor="hand2")
    close_btn.pack(side="right", padx=10)
    close_btn.bind("<Button-1>", lambda e: gui_root.destroy())

    def start_move(e):
        gui_root.x = e.x
        gui_root.y = e.y

    def do_move(e):
        gui_root.geometry(f"+{e.x_root - gui_root.x}+{e.y_root - gui_root.y}")

    title_bar.bind("<Button-1>", start_move)
    title_bar.bind("<B1-Motion>", do_move)

    # ================= CONTENT WITH TABS =================
    content = tk.Frame(gui_root, bg=BG)
    content.pack(fill="both", expand=True, padx=10, pady=10)

    style = ttk.Style()
    style.theme_use('default')
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=CARD, foreground=PINK, font=("Consolas", 10, "bold"), padding=[10, 5])
    style.map("TNotebook.Tab", background=[("selected", PURPLE)], foreground=[("selected", "#ffffff")])

    notebook = ttk.Notebook(content)
    notebook.pack(fill="both", expand=True)

    # ================= SCANNER TAB =================
    scanner_tab = tk.Frame(notebook, bg=BG)
    notebook.add(scanner_tab, text="⚡ SCANNER")

    scanner_content = tk.Frame(scanner_tab, bg=BG)
    scanner_content.pack(fill="both", expand=True, padx=5, pady=5)

    # ================= LOGS TAB ====================
    logs_tab = tk.Frame(notebook, bg=BG)
    notebook.add(logs_tab, text="📜 LOGS")
    logs_text = tk.Text(logs_tab, bg="#000000", fg="#00ff99", insertbackground="#00ff99", font=("Consolas", 10), state="normal")
    logs_text.pack(fill="both", expand=True, padx=5, pady=5)

    # ================= DATABASE TAB =================
    database_tab = tk.Frame(notebook, bg=BG)
    notebook.add(database_tab, text="🗄️DATABASE")

    database_content = tk.Frame(database_tab, bg=BG)
    database_content.pack(fill="both", expand=True, padx=10, pady=10)

    db_filter_online = tk.BooleanVar(value=False)
    db_filter_players = tk.BooleanVar(value=False)
    db_filter_favorites = tk.BooleanVar(value=False)

    db_auto_refresh = False
    db_auto_refresh_job = None

    FAVORITES_FILE = "ressources/favorites.json"
    db_favorites = set()

    def load_favorites():
        nonlocal db_favorites
        try:
            if os.path.exists(FAVORITES_FILE):
                with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    db_favorites = set(data)
        except Exception as e:
            gui_print(f"[FAVORITES] Error loading: {e}")
            db_favorites = set()

    def save_favorites():
        try:
            os.makedirs(os.path.dirname(FAVORITES_FILE), exist_ok=True)
            with open(FAVORITES_FILE, 'w', encoding='utf-8') as f:
                json.dump(list(db_favorites), f)
        except Exception as e:
            gui_print(f"[FAVORITES] Error saving: {e}")

    def toggle_favorite(ip_port):
        nonlocal db_favorites
        if ip_port in db_favorites:
            db_favorites.discard(ip_port)
            gui_print(f"[FAVORITES] Removed {ip_port}", "scan")
        else:
            db_favorites.add(ip_port)
            gui_print(f"[FAVORITES] Added {ip_port}", "online")
        save_favorites()
        refresh_database_list()

    def is_favorite(ip_port):
        return "⭐" if ip_port in db_favorites else "  "

    def refresh_database_list():
        try:
            for item in db_tree.get_children():
                db_tree.delete(item)

            search_query = db_search_var.get() if db_search_var else ""
            if search_query == "🔍 Search servers...":
                search_query = ""

            servers = get_servers_from_db(search_query)

            filtered_servers = []
            for server in servers:
                ip_port = f"{server['ip']}:{server['port']}"
                if db_filter_favorites.get() and ip_port not in db_favorites:
                    continue
                filtered_servers.append(server)

            for server in filtered_servers:
                ip_port = f"{server['ip']}:{server['port']}"
                motd = server.get('motd', '') or ''
                if len(motd) > 45:
                    motd = motd[:42] + "..."
                version = server.get('version', '') or 'Unknown'
                players = f"{server.get('players_online', 0)}/{server.get('players_max', 0)}"
                scanned_at = server.get('scanned_at', '') or ''
                fav = is_favorite(ip_port)
                db_tree.insert('', 'end', values=(fav, ip_port, motd, version, players, scanned_at))

            db_count_label.config(text=f"Servers: {len(filtered_servers)}")

        except Exception as e:
            gui_print(f"[DATABASE] Error loading servers: {e}", "error")

    def toggle_auto_refresh():
        nonlocal db_auto_refresh, db_auto_refresh_job
        db_auto_refresh = not db_auto_refresh

        if db_auto_refresh:
            auto_refresh_btn.config(text="⏸️ Stop", bg=RED)
            gui_print("[DATABASE] Auto-refresh enabled (30s)", "online")
            schedule_auto_refresh()
        else:
            auto_refresh_btn.config(text="▶️ Auto", bg=PURPLE)
            gui_print("[DATABASE] Auto-refresh disabled", "scan")
            if db_auto_refresh_job:
                database_content.after_cancel(db_auto_refresh_job)
                db_auto_refresh_job = None

    def schedule_auto_refresh():
        nonlocal db_auto_refresh_job
        if db_auto_refresh:
            refresh_database_list()
            db_auto_refresh_job = database_content.after(30000, schedule_auto_refresh)

    def select_all_servers():
        for item in db_tree.get_children():
            db_tree.selection_add(item)
        gui_print(f"[DATABASE] Selected {len(db_tree.get_children())} servers", "scan")

    def ping_selected_servers():
        selected = get_selected_servers()
        if not selected:
            gui_print("[DATABASE] No servers selected!", "error")
            return
        gui_print(f"[DATABASE] Pinging {len(selected)} selected servers...", "scan")

        def ping_all():
            for ip_port in selected:
                ping_single_server_from_db(ip_port)
                time.sleep(0.5)

        threading.Thread(target=ping_all, daemon=True).start()

    def delete_selected_servers():
        selected = get_selected_servers()
        if not selected:
            gui_print("[DATABASE] No servers selected!", "error")
            return

        confirm = tk.Toplevel(gui_root)
        confirm.title("Confirm Delete")
        confirm.geometry("400x150")
        confirm.configure(bg=BG)
        confirm.transient(gui_root)
        confirm.grab_set()

        tk.Label(confirm, text=f"Delete {len(selected)} servers?", bg=BG, fg=PINK, font=("Consolas", 14, "bold")).pack(pady=20)

        btn_frame = tk.Frame(confirm, bg=BG)
        btn_frame.pack(pady=10)

        def do_delete():
            for ip_port in selected:
                delete_server_from_db(ip_port)
            confirm.destroy()

        tk.Button(btn_frame, text="✅ Yes, Delete", command=do_delete, bg=RED, fg="#ffffff", font=("Consolas", 12, "bold"), padx=20, pady=5).pack(side="left", padx=5)
        tk.Button(btn_frame, text="❌ Cancel", command=confirm.destroy, bg=CARD, fg=PINK, font=("Consolas", 12, "bold"), padx=20, pady=5).pack(side="left", padx=5)

    def get_selected_servers():
        selected = []
        for item in db_tree.selection():
            values = db_tree.item(item, 'values')
            if values:
                selected.append(values[1])
        return selected

    def ping_single_server_from_db(ip_port):
        try:
            ip, port_str = ip_port.rsplit(':', 1)
            port = int(port_str)
            gui_print(f"[DATABASE] Pinging {ip_port}...", "scan")

            result = ping_single_server(ip, port)
            if result:
                motd = result.get("description", "")
                if isinstance(motd, dict):
                    motd = motd.get("text", "") or str(motd)
                version = result.get("version", {}).get("name", "Unknown")
                p_online = result.get("players", {}).get("online", 0)
                p_max = result.get("players", {}).get("max", 0)
                update_server(ip, port, motd, version, p_online, p_max, "", "")
                gui_print(f"[DATABASE] {ip_port} is ONLINE - {p_online}/{p_max} players", "online")
            else:
                gui_print(f"[DATABASE] {ip_port} is OFFLINE", "error")

            refresh_database_list()
        except Exception as e:
            gui_print(f"[DATABASE] Error pinging {ip_port}: {e}", "error")

    def delete_server_from_db(ip_port):
        try:
            ip, port_str = ip_port.rsplit(':', 1)
            port = int(port_str)
            conn = sqlite3.connect(DATABASE_FILE)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM servers WHERE ip = ? AND port = ?", (ip, port))
            conn.commit()
            conn.close()
            gui_print(f"[DATABASE] Deleted {ip_port}", "scan")
            refresh_database_list()
        except Exception as e:
            gui_print(f"[DATABASE] Error deleting {ip_port}: {e}", "error")

    def copy_to_clipboard(text):
        gui_root.clipboard_clear()
        gui_root.clipboard_append(text)
        gui_root.update()
        gui_print(f"[CLIPBOARD] Copied: {text[:50]}", "online")

    def show_context_menu(event):
        row = db_tree.identify_row(event.y)
        if row:
            db_tree.selection_set(row)
            item = db_tree.item(row)
            values = item['values']
            if values:
                ip_port = values[1]
                motd = values[2]

                menu = tk.Menu(gui_root, tearoff=0, bg=CARD, fg=CYAN,
                              activebackground=PURPLE, activeforeground="#ffffff",
                              font=("Consolas", 10))
                menu.add_command(label="⭐ Toggle Favorite", command=lambda: toggle_favorite(ip_port))
                menu.add_separator()
                menu.add_command(label="📋 Copy IP:Port", command=lambda: copy_to_clipboard(ip_port))
                menu.add_command(label="📋 Copy MOTD", command=lambda: copy_to_clipboard(motd))
                menu.add_separator()
                menu.add_command(label="🔄 Ping Server", command=lambda: ping_single_server_from_db(ip_port))
                menu.add_command(label="❌ Delete", command=lambda: delete_server_from_db(ip_port))
                menu.post(event.x_root, event.y_root)

    # Database Search Frame
    db_search_frame = tk.Frame(database_content, bg=BG)
    db_search_frame.pack(fill="x", pady=(0, 10))

    db_search_var = tk.StringVar()
    db_search_entry = tk.Entry(
        db_search_frame, textvariable=db_search_var,
        bg=CARD, fg="#00ffea", insertbackground=PINK,
        font=("Consolas", 11), bd=2, highlightbackground=PURPLE, highlightthickness=1, width=35
    )
    db_search_entry.pack(side="left", padx=(0, 10))

    def on_db_search_focus_in(e):
        if db_search_entry.get() == "🔍 Search servers...":
            db_search_entry.delete(0, tk.END)
            db_search_entry.config(fg="#ffffff")

    def on_db_search_focus_out(e):
        if db_search_entry.get() == "":
            db_search_entry.insert(0, "🔍 Search servers...")
            db_search_entry.config(fg="#00ffea")

    db_search_entry.bind("<FocusIn>", on_db_search_focus_in)
    db_search_entry.bind("<FocusOut>", on_db_search_focus_out)

    tk.Button(
        db_search_frame, text="🔄 Refresh", command=lambda: refresh_database_list(),
        bg=PURPLE, fg="#ffffff", font=("Consolas", 10, "bold"), bd=0,
        padx=15, pady=5, cursor="hand2", activebackground=PINK, activeforeground="#ffffff"
    ).pack(side="left", padx=(0, 10))

    db_count_label = tk.Label(db_search_frame, text="Servers: 0", bg=BG, fg=PINK, font=("Consolas", 11, "bold"))
    db_count_label.pack(side="right")

    db_filter_frame = tk.Frame(database_content, bg=BG)
    db_filter_frame.pack(fill="x", pady=(0, 10))

    tk.Label(db_filter_frame, text="🔍 Filters:", bg=BG, fg=PINK, font=("Consolas", 10, "bold")).pack(side="left", padx=(0, 10))
    tk.Checkbutton(db_filter_frame, text="⭐ Favorites", variable=db_filter_favorites,
                  bg=BG, fg=CYAN, selectcolor=CARD, activebackground=BG,
                  activeforeground=PINK, font=("Consolas", 9),
                  command=refresh_database_list).pack(side="left", padx=5)

    db_bulk_frame = tk.Frame(database_content, bg=BG)
    db_bulk_frame.pack(fill="x", pady=(0, 10))

    tk.Label(db_bulk_frame, text="📦 Bulk:", bg=BG, fg=PINK, font=("Consolas", 10, "bold")).pack(side="left", padx=(0, 10))
    tk.Button(db_bulk_frame, text="☑️ Select All", command=select_all_servers,
             bg=CARD, fg=CYAN, font=("Consolas", 9), bd=1,
             highlightbackground=PURPLE, padx=10).pack(side="left", padx=5)
    tk.Button(db_bulk_frame, text="🔄 Ping Selected", command=ping_selected_servers,
             bg=PURPLE, fg="#ffffff", font=("Consolas", 9, "bold"), bd=0, padx=10).pack(side="left", padx=5)
    tk.Button(db_bulk_frame, text="❌ Delete Selected", command=delete_selected_servers,
             bg=RED, fg="#ffffff", font=("Consolas", 9, "bold"), bd=0, padx=10).pack(side="left", padx=5)

    auto_refresh_btn = tk.Button(
        db_bulk_frame, text="▶️ Auto", command=toggle_auto_refresh,
        bg=PURPLE, fg="#ffffff", font=("Consolas", 9, "bold"),
        bd=0, padx=15, pady=2, cursor="hand2"
    )
    auto_refresh_btn.pack(side="right", padx=5)

    db_tree_frame = tk.Frame(database_content, bg=CARD, highlightbackground=PURPLE, highlightthickness=2)
    db_tree_frame.pack(fill="both", expand=True)

    db_tree = ttk.Treeview(
        db_tree_frame,
        columns=("fav", "ip_port", "motd", "version", "players", "scanned_at"),
        show="headings",
        style="Database.Treeview"
    )

    db_style = ttk.Style()
    db_style.theme_use("default")
    db_style.configure("Database.Treeview", background="#020202", foreground="#00ffea", fieldbackground="#020202", rowheight=28)
    db_style.configure("Database.Treeview.Heading", background=CARD, foreground=PINK, font=("Consolas", 10, "bold"))
    db_style.map("Database.Treeview", background=[("selected", PURPLE)])

    db_tree.heading("fav", text="⭐")
    db_tree.heading("ip_port", text="IP:Port")
    db_tree.heading("motd", text="MOTD")
    db_tree.heading("version", text="Version")
    db_tree.heading("players", text="Players")
    db_tree.heading("scanned_at", text="Last Scanned")

    db_tree.column("fav", width=40, minwidth=40, anchor="center")
    db_tree.column("ip_port", width=150, minwidth=120)
    db_tree.column("motd", width=280, minwidth=200)
    db_tree.column("version", width=100, minwidth=80)
    db_tree.column("players", width=80, minwidth=70)
    db_tree.column("scanned_at", width=150, minwidth=120)

    db_scroll = tk.Scrollbar(db_tree_frame, orient="vertical", command=db_tree.yview)
    db_tree.configure(yscrollcommand=db_scroll.set)
    db_tree.pack(side="left", fill="both", expand=True)
    db_scroll.pack(side="right", fill="y")

    load_favorites()

    def on_db_search_changed(*args):
        refresh_database_list()

    db_search_var.trace_add("write", on_db_search_changed)

    def on_db_server_double_click(event):
        selection = db_tree.selection()
        if selection:
            item = db_tree.item(selection[0])
            values = item['values']
            if values:
                region = db_tree.identify_region(event.x, event.y)
                column = db_tree.identify_column(event.x)
                ip_port = values[1]

                if column == '#1' or (region == "cell" and event.x < 50):
                    toggle_favorite(ip_port)
                    return

                try:
                    ip, port_str = ip_port.rsplit(':', 1)
                    port = int(port_str)
                    servers = get_servers_from_db("")
                    server_data = next((s for s in servers if s['ip'] == ip and s['port'] == port), None)
                    if server_data:
                        open_server_detail(server_data)
                except Exception as e:
                    print(f"Error opening server detail: {e}")

    db_tree.bind("<Double-1>", on_db_server_double_click)
    db_tree.bind("<Button-3>", show_context_menu)
    db_tree.bind("<Control-1>", show_context_menu)

    refresh_database_list()

    # ================= ADVANCED TAB =================
    advanced_tab = tk.Frame(notebook, bg=BG)
    notebook.add(advanced_tab, text="📈 ADVANCED")

    advanced_content = tk.Frame(advanced_tab, bg=BG)
    advanced_content.pack(fill="both", expand=True, padx=5, pady=5)

    advanced_panel = tk.Frame(advanced_content, bg=CARD, highlightbackground=PURPLE, highlightthickness=2)
    advanced_panel.pack(fill="both", expand=True, padx=5, pady=5)

    tk.Label(advanced_panel, text="📊 ADVANCED STATISTICS", bg=CARD, fg=PURPLE, font=("Consolas", 14, "bold")).pack(pady=10)

    stats_grid = tk.Frame(advanced_panel, bg=CARD)
    stats_grid.pack(pady=10)

    tk.Label(stats_grid, text="🔍 Scans/Min", bg=CARD, fg=PINK, font=("Consolas", 10, "bold")).grid(row=0, column=0, padx=20, pady=5)
    advanced_stats_labels["scans_per_min"] = tk.Label(stats_grid, text="0.0", bg=CARD, fg="#00ffea", font=("Consolas", 16, "bold"))
    advanced_stats_labels["scans_per_min"].grid(row=1, column=0, padx=20, pady=5)

    tk.Label(stats_grid, text="🎯 Found/Min", bg=CARD, fg=PINK, font=("Consolas", 10, "bold")).grid(row=0, column=1, padx=20, pady=5)
    advanced_stats_labels["found_per_min"] = tk.Label(stats_grid, text="0.0", bg=CARD, fg="#00ffea", font=("Consolas", 16, "bold"))
    advanced_stats_labels["found_per_min"].grid(row=1, column=1, padx=20, pady=5)

    tk.Label(stats_grid, text="⚡ Current Rate", bg=CARD, fg=PINK, font=("Consolas", 10, "bold")).grid(row=2, column=0, padx=20, pady=5)
    advanced_stats_labels["current_rate"] = tk.Label(stats_grid, text="0.0/s", bg=CARD, fg="#00ffea", font=("Consolas", 16, "bold"))
    advanced_stats_labels["current_rate"].grid(row=3, column=0, padx=20, pady=5)

    tk.Label(stats_grid, text="🏆 Peak Scans/Min", bg=CARD, fg=PINK, font=("Consolas", 10, "bold")).grid(row=2, column=1, padx=20, pady=5)
    advanced_stats_labels["peak_scans"] = tk.Label(stats_grid, text="0.0", bg=CARD, fg="#ff00aa", font=("Consolas", 16, "bold"))
    advanced_stats_labels["peak_scans"].grid(row=3, column=1, padx=20, pady=5)

    graph_frame = tk.Frame(advanced_panel, bg="#020202", highlightbackground=PURPLE, highlightthickness=1)
    graph_frame.pack(fill="both", expand=True, padx=20, pady=10)

    tk.Label(graph_frame, text="📈 10-Second Scan History", bg="#020202", fg=PURPLE, font=("Consolas", 11, "bold")).pack(pady=5)

    scan_graph_canvas = tk.Canvas(graph_frame, bg="#020202", height=250, highlightthickness=0)
    scan_graph_canvas.pack(fill="both", expand=True, padx=10, pady=5)

    for i in range(6):
        y = 30 + i * 40
        scan_graph_canvas.create_line(50, y, 400, y, fill="#1a1a1a", tags="grid")

    for i in range(10):
        x = 55 + i * 35
        scan_graph_canvas.create_text(x, 240, text=f"{9-i}s", fill="#666666", font=("Consolas", 8), tags="grid")

    scan_graph_canvas.create_text(20, 20, text="MAX", fill="#666666", font=("Consolas", 8), tags="grid")

    gui_root.after(500, gui_update_stats)
    gui_root.after(1000, gui_update_advanced_stats)
    gui_root.after(100, gui_drain_message_queue)

    # ================= YOURSERVERS TAB (archived) =================
    yourservers_tab = tk.Frame(notebook, bg=BG)

    yourservers_content = tk.Frame(yourservers_tab, bg=BG)
    yourservers_content.pack(fill="both", expand=True, padx=10, pady=10)

    search_btn_frame = tk.Frame(yourservers_content, bg=BG)
    search_btn_frame.pack(fill="x", pady=(0, 10))

    servers_search_var = tk.StringVar()
    search_entry = tk.Entry(
        search_btn_frame, textvariable=servers_search_var,
        bg=CARD, fg="#00ffea", insertbackground=PINK,
        font=("Consolas", 10), bd=2, highlightbackground=PURPLE, highlightthickness=1, width=30
    )
    search_entry.pack(side="left", padx=(0, 10))
    search_entry.insert(0, "Search servers...")

    def on_search_focus_in(e):
        if search_entry.get() == "Search servers...":
            search_entry.delete(0, tk.END)
            search_entry.config(fg="#ffffff")

    def on_search_focus_out(e):
        if search_entry.get() == "":
            search_entry.insert(0, "Search servers...")
            search_entry.config(fg="#00ffea")

    search_entry.bind("<FocusIn>", on_search_focus_in)
    search_entry.bind("<FocusOut>", on_search_focus_out)

    def on_search_changed(*args):
        refresh_servers_list()

    servers_search_var.trace_add("write", on_search_changed)

    tk.Button(
        search_btn_frame, text="🚀 Initialize", command=run_server_checker,
        bg=PURPLE, fg="#ffffff", font=("Consolas", 10, "bold"), bd=0,
        padx=15, pady=5, cursor="hand2", activebackground=PINK, activeforeground="#ffffff"
    ).pack(side="left", padx=(0, 10))

    tk.Button(
        search_btn_frame, text="🔄 Refresh", command=refresh_servers_list,
        bg=CARD, fg=PINK, font=("Consolas", 10, "bold"), bd=2,
        highlightbackground=PURPLE, highlightthickness=2,
        padx=15, pady=5, cursor="hand2", activebackground=PURPLE, activeforeground="#ffffff"
    ).pack(side="left")

    server_count_label = tk.Label(search_btn_frame, text="Servers: 0", bg=BG, fg=PINK, font=("Consolas", 10, "bold"))
    server_count_label.pack(side="right")

    tree_frame = tk.Frame(yourservers_content, bg=CARD, highlightbackground=PURPLE, highlightthickness=2)
    tree_frame.pack(fill="both", expand=True)

    servers_tree = ttk.Treeview(
        tree_frame,
        columns=("ip_port", "motd", "version", "players", "scanned_at"),
        show="headings",
        style="Custom.Treeview"
    )

    style2 = ttk.Style()
    style2.theme_use("default")
    style2.configure("Custom.Treeview", background="#020202", foreground="#00ffea", fieldbackground="#020202", rowheight=25)
    style2.configure("Custom.Treeview.Heading", background=CARD, foreground=PINK, font=("Consolas", 10, "bold"))
    style2.map("Custom.Treeview", background=[("selected", PURPLE)])

    servers_tree.heading("ip_port", text="IP:Port")
    servers_tree.heading("motd", text="MOTD")
    servers_tree.heading("version", text="Version")
    servers_tree.heading("players", text="Players")
    servers_tree.heading("scanned_at", text="Last Scanned")

    servers_tree.column("ip_port", width=150, minwidth=100)
    servers_tree.column("motd", width=250, minwidth=150)
    servers_tree.column("version", width=120, minwidth=80)
    servers_tree.column("players", width=80, minwidth=60)
    servers_tree.column("scanned_at", width=150, minwidth=100)

    tree_scroll = tk.Scrollbar(tree_frame, orient="vertical", command=servers_tree.yview)
    servers_tree.configure(yscrollcommand=tree_scroll.set)
    servers_tree.pack(side="left", fill="both", expand=True)
    tree_scroll.pack(side="right", fill="y")

    def on_server_double_click(event):
        selection = servers_tree.selection()
        if selection:
            item = servers_tree.item(selection[0])
            values = item['values']
            if values:
                ip_port = values[0]
                try:
                    ip, port_str = ip_port.rsplit(':', 1)
                    port = int(port_str)
                    servers = get_servers_from_db("")
                    server_data = next((s for s in servers if s['ip'] == ip and s['port'] == port), None)
                    if server_data:
                        open_server_detail(server_data)
                except Exception as e:
                    print(f"Error opening server detail: {e}")

    servers_tree.bind("<Double-1>", on_server_double_click)
    refresh_servers_list()

    # ============ Scanner panels ===============
    servers_panel = tk.Frame(scanner_content, bg=CARD, highlightbackground=PURPLE, highlightthickness=2)
    servers_panel.pack(side="left", fill="both", expand=True, padx=(0, 8))

    log_panel = tk.Frame(scanner_content, bg=CARD, highlightbackground=PURPLE, highlightthickness=2)
    log_panel.pack(side="left", fill="both", expand=True, padx=(0, 8))

    tk.Label(log_panel, text="⚡ LIVE SCAN LOG", bg=CARD, fg=PURPLE, font=("Consolas", 12, "bold")).pack(pady=10)

    scan_log_text = tk.Text(
        log_panel, bg="#020202", fg="#00ffea", font=("Consolas", 9),
        insertbackground=PINK, bd=0, highlightthickness=0, wrap="word"
    )
    scan_log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    scan_log_text.tag_config("scan", foreground="#ffff00")
    scan_log_text.tag_config("none", foreground="#ff0055")
    scan_log_text.tag_config("online", foreground="#00ff99")
    scan_log_text.tag_config("empty", foreground="#00ffaa")
    scan_log_text.tag_config("webhook", foreground="#00e1ff")
    scan_log_text.tag_config("error", foreground="#ff00ff")

    # ================= STATS PANEL =================
    stats_panel = tk.Frame(scanner_content, bg=CARD, highlightbackground=PURPLE, highlightthickness=2)
    stats_panel.pack(side="right", fill="y", ipadx=10)

    tk.Label(stats_panel, text="📊 STATS", bg=CARD, fg=PURPLE, font=("Consolas", 12, "bold")).pack(pady=15)

    keys = ["Scanned", "Found", "With Players", "Server scanner per hour", "Webhooks Sent", "Active Scanners", "Run Progress"]

    for k in keys:
        tk.Label(stats_panel, text=k, bg=CARD, fg=PURPLE, font=("Segoe UI", 9)).pack(pady=(5, 0))
        stats_labels[k] = tk.Label(
            stats_panel,
            text="-" if k == "Run Progress" else "0",
            bg=CARD, fg=PINK, font=("Consolas", 14, "bold")
        )
        stats_labels[k].pack(pady=(0, 10))

    # ================= SETTINGS TAB =================
    settings_tab = tk.Frame(notebook, bg=BG)
    notebook.add(settings_tab, text="⚙️ SETTINGS")

    settings_frame = tk.Frame(settings_tab, bg=CARD, highlightbackground=PURPLE, highlightthickness=2)
    settings_frame.pack(fill="both", expand=True, padx=10, pady=10)

    settings_canvas = tk.Canvas(settings_frame, bg=BG, highlightthickness=0)
    scrollbar = tk.Scrollbar(settings_frame, orient="vertical", command=settings_canvas.yview)
    scrollable_frame = tk.Frame(settings_canvas, bg=BG)

    scrollable_frame.bind("<Configure>", lambda e: settings_canvas.configure(scrollregion=settings_canvas.bbox("all")))
    settings_canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    settings_canvas.configure(yscrollcommand=scrollbar.set)
    settings_canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    tk.Label(scrollable_frame, text="⚙️ CONFIGURATION", bg=BG, fg=PURPLE, font=("Consolas", 16, "bold")).pack(pady=20)

    def make_setting(label, default):
        tk.Label(scrollable_frame, text=label, bg=BG, fg=PINK, font=("Consolas", 10, "bold")).pack(pady=(15, 5))
        entry = tk.Entry(scrollable_frame, bg=CARD, fg="#00ffea", insertbackground=PINK,
                        font=("Consolas", 10 if label != "WEBHOOK URL" else 9),
                        width=70 if label == "WEBHOOK URL" else 20,
                        bd=2, highlightbackground=PURPLE, highlightthickness=1)
        entry.pack(pady=5, padx=20)
        entry.insert(0, default)
        return entry

    webhook_entry  = make_setting("WEBHOOK URL",          config.WEBHOOK_URL)
    port_entry     = make_setting("PORT",                  str(config.PORT))
    timeout_entry  = make_setting("TIMEOUT (seconds)",    str(config.TIMEOUT))
    concurrency_entry = make_setting("CONCURRENCY",       str(config.CONCURRENCY))
    webhost_entry  = make_setting("WEB HOST",              config.WEB_HOST)
    webport_entry  = make_setting("WEB PORT",              str(config.WEB_PORT))

    settings_status = tk.Label(scrollable_frame, text="", bg=BG, fg="#00ff99", font=("Consolas", 10, "bold"))
    settings_status.pack(pady=10)

    settings_btn_frame = tk.Frame(scrollable_frame, bg=BG)
    settings_btn_frame.pack(pady=20)

    def save_settings():
        try:
            if save_config_settings(
                webhook_entry.get(), int(port_entry.get()),
                int(timeout_entry.get()), int(concurrency_entry.get()),
                webhost_entry.get(), int(webport_entry.get())
            ):
                settings_status.config(text="✅ Settings saved! Restart required.", fg="#00ff99")
            else:
                settings_status.config(text="❌ Failed to save settings!", fg="#ff0055")
        except ValueError:
            settings_status.config(text="❌ Invalid number values!", fg="#ff0055")

    def reset_settings():
        for entry, val in [
            (webhook_entry, config.WEBHOOK_URL), (port_entry, str(config.PORT)),
            (timeout_entry, str(config.TIMEOUT)), (concurrency_entry, str(config.CONCURRENCY)),
            (webhost_entry, config.WEB_HOST), (webport_entry, str(config.WEB_PORT))
        ]:
            entry.delete(0, tk.END)
            entry.insert(0, val)
        settings_status.config(text="🔄 Settings reset to current values", fg="#ffff00")

    tk.Button(settings_btn_frame, text="💾 SAVE", command=save_settings,
             bg=PURPLE, fg="#ffffff", font=("Consolas", 12, "bold"), bd=0,
             padx=20, pady=8, cursor="hand2", activebackground=PINK, activeforeground="#ffffff").pack(side="left", padx=10)

    tk.Button(settings_btn_frame, text="🔄 RESET", command=reset_settings,
             bg=CARD, fg=PINK, font=("Consolas", 12, "bold"), bd=2,
             highlightbackground=PURPLE, highlightthickness=2,
             padx=20, pady=8, cursor="hand2", activebackground=PURPLE, activeforeground="#ffffff").pack(side="left", padx=10)

    # ================= CHANGELOG TAB =================
    changelog_tab = tk.Frame(notebook, bg=BG)
    notebook.add(changelog_tab, text="🆕 CHANGELOG")

    changelog_frame = tk.Frame(changelog_tab, bg=CARD, highlightbackground=PURPLE, highlightthickness=2)
    changelog_frame.pack(fill="both", expand=True, padx=10, pady=10)

    changelog_canvas = tk.Canvas(changelog_frame, bg=BG, highlightthickness=0)
    changelog_scrollbar = tk.Scrollbar(changelog_frame, orient="vertical", command=changelog_canvas.yview)
    changelog_content_frame = tk.Frame(changelog_canvas, bg=BG)

    changelog_content_frame.bind("<Configure>", lambda e: changelog_canvas.configure(scrollregion=changelog_canvas.bbox("all")))
    changelog_canvas.create_window((0, 0), window=changelog_content_frame, anchor="nw")
    changelog_canvas.configure(yscrollcommand=changelog_scrollbar.set)
    changelog_canvas.pack(side="left", fill="both", expand=True)
    changelog_scrollbar.pack(side="right", fill="y")

    changelog_text = tk.Text(
        changelog_content_frame, bg=BG, fg="#ffffff", font=("Consolas", 12),
        wrap="word", bd=0, highlightbackground=PURPLE, highlightthickness=2, padx=10, pady=10
    )
    changelog_text.pack(fill="both", expand=True, padx=20, pady=20)

    changelog_content = """Changelog - Cyber MCS Scanner v2.8

Version 2.8 - "Better Webhooks" (2026-02-21)
- 🌹 Improved Discord embeds: cleaner layout, timestamps, server favicon thumbnails
- 🔄 Retry logic (3 attempts) with proper rate-limit handling (429 backoff)
- 🎨 MOTD § color code stripping so Discord shows clean text
- 🟢 Dynamic embed colors: bright green for 3+ players, gold for 1-2, orange for empty
- ⛔ Disconnect message field only shown when meaningful (not N/A)
- 🤖 Webhook bot now shows as "🌹 MCS Scanner" in Discord

Version 2.7 - "goofy ahh update by proxyshlart" (2026-02-21)
- Made webhooks better
- New bot that joins cracked servers to see if they are cracked
- yes

Version 2.7 - "Data City" (2026-02-15)
- Added Database tab where you can view all scanned servers, search, and open details

Version 2.6 - "Neon Nights" (2026-02-14)
- 🌟 Added Advanced Stats tab with real-time performance metrics
- 🌟 Added 10-second scan history graph with dynamic scaling and color gradients
- 🌟 Added changelog and credits tabs with cyberpunk design
- 🔧 Improved performance and stability
- 🔧 Various bug fixes and optimizations
- 🚀 Enhanced user experience and functionality
- 🎉 Better merging between Master and Worker modes for seamless multi-instance operation

Version 2.5 - "Cyberpunk Edition" (2026-02-13)
- 🎨 Complete GUI overhaul with new cyberpunk theme and animations
- 🚀 Improved scanning performance and stability
- 🔧 Various bug fixes and optimizations

"""
    changelog_text.insert("1.0", changelog_content)
    changelog_text.config(state="disabled")

    # ================= CREDITS TAB =================
    credits_tab = tk.Frame(notebook, bg=BG)
    notebook.add(credits_tab, text="💜 CREDITS")

    credits_frame = tk.Frame(credits_tab, bg=CARD, highlightbackground=PURPLE, highlightthickness=2)
    credits_frame.pack(fill="both", expand=True, padx=10, pady=10)

    credits_canvas = tk.Canvas(credits_frame, bg=BG, highlightthickness=0)
    credits_scrollbar = tk.Scrollbar(credits_frame, orient="vertical", command=credits_canvas.yview)
    credits_content_frame = tk.Frame(credits_canvas, bg=BG)

    credits_content_frame.bind("<Configure>", lambda e: credits_canvas.configure(scrollregion=credits_canvas.bbox("all")))
    credits_canvas.create_window((0, 0), window=credits_content_frame, anchor="nw")
    credits_canvas.configure(yscrollcommand=credits_scrollbar.set)
    credits_canvas.pack(side="left", fill="both", expand=True)
    credits_scrollbar.pack(side="right", fill="y")

    ascii_text = tk.Text(credits_content_frame, bg=BG, fg=PINK, font=("Consolas", 8), bd=0, highlightthickness=0, wrap="word", height=40, width=80)
    ascii_text.pack(pady=20)
    ascii_text.insert("1.0", load_ascii_art())
    ascii_text.config(state="disabled")

    tk.Label(credits_content_frame, text="🌹 CYBER MCS SCANNER 🌹", bg=BG, fg=PURPLE, font=("Consolas", 20, "bold")).pack(pady=10)
    tk.Label(credits_content_frame, text="Version 2.8 - Better Webhooks", bg=BG, fg=NEON, font=("Consolas", 12)).pack(pady=5)
    tk.Frame(credits_content_frame, bg=PURPLE, height=2, width=400).pack(pady=20)
    tk.Label(credits_content_frame, text="✨ FEATURES", bg=BG, fg=PINK, font=("Consolas", 14, "bold")).pack(pady=10)

    for feature in [
        "🔍 High-performance Minecraft server scanner",
        "🌐 Multi-instance support (Master/Worker mode)",
        "📊 Real-time statistics and monitoring",
        "🔔 Discord webhook notifications with retry logic",
        "🎨 Cyberpunk-themed GUI",
        "⚙️ Configurable settings",
        "🚀 Async/await for maximum performance",
        "🛠️ Local Database and browser"
    ]:
        tk.Label(credits_content_frame, text=feature, bg=BG, fg="#00ffea", font=("Segoe UI", 10)).pack(pady=2)

    tk.Label(credits_content_frame, text="🛠️ Developers", bg=BG, fg=PINK, font=("Consolas", 14, "bold")).pack(pady=10)
    for dev in ["🌹 n3xtgen  aka EliasPython 🌹", "🐍 m3gamichi  aka m3gamichi 🐍"]:
        tk.Label(credits_content_frame, text=dev, bg=BG, fg="#00ffea", font=("Segoe UI", 10)).pack(pady=2)

    # ================= BOTTOM STATUS BAR =================
    status_bar = tk.Frame(gui_root, bg=CARD, highlightbackground=PURPLE, highlightthickness=1, height=25)
    status_bar.pack(fill="x", side="bottom", padx=10, pady=(0, 10))
    status_bar.pack_propagate(False)

    status_label = tk.Label(
        status_bar, text="🆕 v2.8: Better Webhooks",
        bg=CARD, fg="#00ffea", font=("Consolas", 9), cursor="hand2"
    )
    status_label.pack(side="left", padx=10, pady=2)
    status_label.bind("<Button-1>", lambda e: notebook.select(changelog_tab))

    tk.Label(status_bar, text="v2.8 | MCS Scanner", bg=CARD, fg=PINK, font=("Consolas", 9, "bold")).pack(side="right", padx=10, pady=2)

    gui_root.mainloop()


# ========= ASN RANGES =========
ASN_RANGES = [
    ("88.198.0.0", 16), ("95.216.0.0", 15), ("116.202.0.0", 16),
    ("138.201.0.0", 16), ("159.69.0.0", 16),
    ("51.38.0.0", 16), ("54.36.0.0", 16), ("145.239.0.0", 16),
    ("137.74.0.0", 16),
    ("142.93.0.0", 16), ("159.65.0.0", 16), ("167.99.0.0", 16),
    ("5.189.0.0", 16), ("37.228.0.0", 16), ("185.228.0.0", 16),
    ("89.58.0.0", 16), ("46.38.0.0", 16),
    ("18.0.0.0", 8), ("3.0.0.0", 8),
    ("20.0.0.0", 8),
    ("34.0.0.0", 8),
    ("162.243.0.0", 16), ("198.199.0.0", 16), ("104.248.0.0", 16),
    ("207.148.0.0", 16), ("138.68.0.0", 16), ("165.227.0.0", 16),
    ("157.230.0.0", 16), ("104.236.0.0", 16), ("45.55.0.0", 16),
    ("64.62.0.0", 16), ("45.79.0.0", 16), ("149.56.0.0", 16),
    ("192.241.128.0", 17), ("185.117.0.0", 16), ("213.32.0.0", 16),
    ("46.105.0.0", 16), ("185.104.0.0", 16), ("91.121.0.0", 16), ("185.6.0.0", 16),
    ("5.39.0.0", 16), ("31.13.0.0", 16), ("46.101.0.0", 16), ("51.15.0.0", 16),
    ("62.75.0.0", 16), ("77.73.0.0", 16), ("80.67.0.0", 16), ("104.0.0.0", 16),
    ("107.170.0.0", 16), ("173.194.0.0", 16), ("74.125.0.0", 16), ("96.0.0.0", 16),
    ("103.4.0.0", 16), ("116.31.0.0", 16), ("119.28.0.0", 16), ("123.125.0.0", 16),
    ("177.53.0.0", 16), ("179.43.0.0", 16), ("181.224.0.0", 16), ("41.0.0.0", 16),
    ("102.66.0.0", 16), ("154.0.0.0", 16), ("103.20.0.0", 16), ("203.0.0.0", 16),
    ("1.0.0.0", 16), ("185.8.0.0", 16), ("185.9.0.0", 16), ("178.62.0.0", 16),
    ("159.203.0.0", 16), ("157.230.0.0", 16),
]

ASN_PROB = getattr(config, 'ASN_PROB', 0.5)
ASN_EXPAND_BITS = getattr(config, 'ASN_EXPAND_BITS', 4)


# ========= IP UTILS =========
def ip_to_int(ip):
    a, b, c, d = map(int, ip.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d

def int_to_ip(i):
    return ".".join(str((i >> s) & 255) for s in (24, 16, 8, 0))

def random_from_cidr(base, mask, expand_bits: int = 0):
    base_int = ip_to_int(base)
    new_mask = max(8, mask - expand_bits)
    host_bits = 32 - new_mask
    rand = random.randint(1, (1 << host_bits) - 2)
    return int_to_ip(base_int + rand)

def random_ip():
    if random.random() < ASN_PROB:
        base, mask = random.choice(ASN_RANGES)
        return random_from_cidr(base, mask, ASN_EXPAND_BITS)

    while True:
        a = random.randint(1, 223)
        b = random.randint(0, 255)
        c = random.randint(0, 255)
        d = random.randint(1, 254)

        if a in (10, 127, 0): continue
        if a == 169 and b == 254: continue
        if a == 172 and 16 <= b <= 31: continue
        if a == 192 and b == 168: continue
        if a >= 224: continue
        if a == 100 and 64 <= b <= 127: continue

        return f"{a}.{b}.{c}.{d}"


# ========= VARINT =========
def encode_varint(v):
    out = b""
    while True:
        b = v & 0x7F
        v >>= 7
        out += struct.pack("B", b | (0x80 if v else 0))
        if not v:
            return out

def decode_varint(sock):
    num = 0
    for i in range(5):
        b = sock.recv(1)
        if not b:
            return None
        b = b[0]
        num |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return num
    return None


# ========= MINECRAFT PING =========
def ping(ip):
    try:
        s = socket.socket()
        s.settimeout(config.TIMEOUT)
        s.connect((ip, config.PORT))

        handshake = (
            encode_varint(0) +
            encode_varint(754) +
            encode_varint(len(ip)) + ip.encode() +
            struct.pack(">H", config.PORT) +
            encode_varint(1)
        )

        s.sendall(encode_varint(len(handshake)) + handshake)
        s.sendall(b"\x01\x00")

        decode_varint(s)
        decode_varint(s)
        length = decode_varint(s)

        data = s.recv(length)
        s.close()
        return json.loads(data.decode())
    except:
        return None


# ========= CRACKED / WHITELIST CHECKER =========
def _mc_varint(v):
    out = b""
    while True:
        b = v & 0x7F
        v >>= 7
        out += struct.pack("B", b | (0x80 if v else 0))
        if not v:
            return out

def _mc_string(s):
    data = s.encode("utf-8")
    return _mc_varint(len(data)) + data

def _mc_read_varint(sock):
    result = 0
    for i in range(5):
        b = sock.recv(1)
        if not b:
            return None
        val = b[0]
        result |= (val & 0x7F) << (7 * i)
        if not (val & 0x80):
            return result
    return None

def _mc_read_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf

def _parse_varint_buf(data, offset=0):
    result = 0
    for i in range(5):
        if offset >= len(data):
            raise ValueError("buffer too short")
        val = data[offset]; offset += 1
        result |= (val & 0x7F) << (7 * i)
        if not (val & 0x80):
            return result, offset
    raise ValueError("varint too long")

def _parse_reason(raw: str) -> str:
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            text = obj.get("text") or obj.get("translate") or ""
            with_args = obj.get("with", [])
            if with_args:
                args_str = ", ".join(
                    a.get("text", str(a)) if isinstance(a, dict) else str(a)
                    for a in with_args
                )
                text = f"{text} ({args_str})" if text else args_str
            return text.strip() or raw[:100]
        return str(obj)[:100]
    except Exception:
        return raw[:100]


def _check_cracked_sync(ip, port, username, proto_version):
    import zlib

    compression_threshold = -1

    def read_packet_raw(sock):
        length = _mc_read_varint(sock)
        if length is None:
            return None, None, None
        raw = _mc_read_exact(sock, length)

        if compression_threshold >= 0:
            data_length, offset = _parse_varint_buf(raw)
            if data_length == 0:
                payload = raw[offset:]
            else:
                payload = zlib.decompress(raw[offset:])
        else:
            payload = raw

        pkt_id, offset = _parse_varint_buf(payload)
        return pkt_id, payload[offset:], payload

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(8)
        s.connect((ip, port))

        hs_payload = (
            _mc_varint(0x00) +
            _mc_varint(proto_version) +
            _mc_string(ip) +
            struct.pack(">H", port) +
            _mc_varint(2)
        )
        s.sendall(_mc_varint(len(hs_payload)) + hs_payload)

        ls_data = _mc_string(username)
        if proto_version >= 761:
            ls_data += b"\x00" * 16
        ls_payload = _mc_varint(0x00) + ls_data
        s.sendall(_mc_varint(len(ls_payload)) + ls_payload)

        while True:
            pkt_id, payload, _ = read_packet_raw(s)
            if pkt_id is None:
                s.close()
                return "error:connection closed"

            _check_log(f"{ip}:{port} login pkt={pkt_id:#x} len={len(payload)}")

            if pkt_id == 0x00:
                reason_len, off = _parse_varint_buf(payload)
                reason_raw = payload[off:off + reason_len].decode("utf-8", errors="replace")
                reason_clean = _parse_reason(reason_raw).lower()
                _check_log(f"{ip}:{port} disconnect: {reason_clean[:150]}")
                s.close()
                if any(k in reason_clean for k in ("whitelist", "white-list", "not whitelisted")):
                    return "cracked_whitelist"
                if any(k in reason_clean for k in ("verify", "authenticate", "premium", "invalid session")):
                    return "online_mode"
                if "internal exception" in reason_clean and "decode" in reason_clean:
                    return "error:bad packet format"
                return f"cracked_plugin:{reason_clean[:100]}"

            elif pkt_id == 0x01:
                should_authenticate = True
                try:
                    if proto_version >= 766:
                        off = 0
                        sid_len, off = _parse_varint_buf(payload, off)
                        off += sid_len
                        pk_len, off = _parse_varint_buf(payload, off)
                        off += pk_len
                        vt_len, off = _parse_varint_buf(payload, off)
                        off += vt_len
                        if off < len(payload):
                            should_authenticate = bool(payload[off])
                except Exception:
                    pass
                s.close()
                if not should_authenticate:
                    _check_log(f"{ip}:{port} encryption request but should_authenticate=false → cracked")
                    return "cracked_open"
                return "online_mode"

            elif pkt_id == 0x02:
                ack = _mc_varint(0x03)
                s.sendall(_mc_varint(len(ack)) + ack)

                s.settimeout(8)
                while True:
                    pkt_id2, payload2, _ = read_packet_raw(s)
                    if pkt_id2 is None:
                        s.close()
                        return "cracked_open"

                    _check_log(f"{ip}:{port} config pkt={pkt_id2:#x} len={len(payload2)}")

                    if pkt_id2 == 0x00:
                        reason_len, off = _parse_varint_buf(payload2)
                        reason_raw = payload2[off:off + reason_len].decode("utf-8", errors="replace")
                        reason_clean = _parse_reason(reason_raw).lower()
                        _check_log(f"{ip}:{port} config disconnect: {reason_clean[:150]}")
                        s.close()
                        if any(k in reason_clean for k in ("whitelist", "white-list", "not whitelisted")):
                            return "cracked_whitelist"
                        if any(k in reason_clean for k in ("verify", "authenticate", "premium", "invalid session")):
                            return "online_mode"
                        return f"cracked_plugin:{reason_clean[:100]}"

                    elif pkt_id2 == 0x03:
                        s.close()
                        return "cracked_open"

                    elif pkt_id2 == 0x01:
                        pong = _mc_varint(0x01) + payload2
                        s.sendall(_mc_varint(len(pong)) + pong)

            elif pkt_id == 0x03:
                threshold, _ = _parse_varint_buf(payload)
                compression_threshold = threshold
                _check_log(f"{ip}:{port} compression enabled threshold={threshold}")

            elif pkt_id == 0x04:
                msg_id, _ = _parse_varint_buf(payload)
                response = _mc_varint(0x02) + _mc_varint(msg_id) + b"\x00"
                s.sendall(_mc_varint(len(response)) + response)

            else:
                _check_log(f"{ip}:{port} unknown login pkt {pkt_id:#x}, skipping")

    except socket.timeout:
        return "timeout"
    except Exception as e:
        return f"error:{e}"


# ========= CHECK LOG CONSOLE =========
CHECK_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check_log.txt")
_check_log_lock = threading.Lock()
_check_console_started = False

def _start_check_console():
    global _check_console_started
    if _check_console_started:
        return
    _check_console_started = True
    try:
        open(CHECK_LOG_FILE, "w", encoding="utf-8").close()
    except Exception:
        pass
    cmd = (
        f'start "CHECK LOG" cmd /k "chcp 65001 > nul && powershell -Command '
        f'Get-Content -Wait -Path \'{CHECK_LOG_FILE}\'"'
    )
    try:
        subprocess.Popen(cmd, shell=True)
    except Exception as e:
        print(f"[CHECK] Failed to open log console: {e}")

def _check_log(message):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {message}\n"
    with _check_log_lock:
        try:
            with open(CHECK_LOG_FILE, "a", encoding="utf-8-sig") as f:
                f.write(line)
        except Exception:
            pass

_start_check_console()


async def check_cracked_and_whitelist(ip, port, proto_version=754):
    loop = asyncio.get_running_loop()
    username = "Sc4nB0t" + str(random.randint(100, 999))
    result = await loop.run_in_executor(executor, _check_cracked_sync, ip, port, username, proto_version)

    _check_log(f"{ip}:{port} → raw={result}")

    if result == "cracked_open":
        cw = {"cracked": True,  "whitelist": False, "join_msg": "Cracked ✅ | No whitelist ✅"}
    elif result == "cracked_whitelist":
        cw = {"cracked": True,  "whitelist": True,  "join_msg": "Cracked ✅ | Whitelisted ❌"}
    elif result == "online_mode":
        cw = {"cracked": False, "whitelist": None,  "join_msg": "Online-mode ❌"}
    elif result == "timeout":
        cw = {"cracked": None,  "whitelist": None,  "join_msg": "Timed out"}
    elif result.startswith("cracked_plugin:"):
        reason = result[len("cracked_plugin:"):]
        cw = {"cracked": True,  "whitelist": None,  "join_msg": f"Cracked ✅ | Plugin gate: {reason[:60]}"}
    else:
        cw = {"cracked": None, "whitelist": None, "join_msg": result[:80]}

    _check_log(f"{ip}:{port} → {cw['join_msg']}")
    return cw


# ========= SCAN =========
async def scan(ip, sem):
    global scanned, found, with_players, sent_count

    async with sem:
        try:
            with counter_lock:
                scanned += 1
        except Exception:
            pass

        try:
            with scan_times_lock:
                scan_times.append(time.time())
        except Exception:
            pass

        try:
            set_title()
            gui_print(f"[SCAN] {ip}", "scan")
        except Exception:
            pass

        try:
            data = await asyncio.get_running_loop().run_in_executor(executor, ping, ip)
        except asyncio.CancelledError:
            raise
        except Exception:
            data = None

        if not data:
            try:
                gui_print(f"[NONE] {ip}", "none")
            except Exception:
                pass
            return

        try:
            with counter_lock:
                found += 1
        except Exception:
            pass

        try:
            with recent_found_lock:
                recent_found.appendleft(f"{ip}:{config.PORT}")
        except Exception:
            pass

        try:
            with found_times_lock:
                found_times.append(time.time())
        except Exception:
            pass

        try:
            set_title()
        except Exception:
            pass

        try:
            players = data["players"]["online"]
            maxp = data["players"]["max"]
            version = data["version"]["name"]
            motd = data["description"]
            if isinstance(motd, dict):
                motd = motd.get("text", "")
        except (KeyError, TypeError):
            return

        # ---- Cracked / Whitelist check ----
        try:
            proto_ver = data.get("version", {}).get("protocol", 754)
            gui_print(f"[CHECK] {ip} — probing login (proto {proto_ver})...", "scan")
            cw = await check_cracked_and_whitelist(ip, config.PORT, proto_ver)
            cracked_val   = cw.get("cracked")
            whitelist_val = cw.get("whitelist")
            join_msg      = cw.get("join_msg", "?")
            gui_print(f"[CHECK] {ip} → {join_msg}", "webhook")
        except Exception as e:
            cracked_val   = None
            whitelist_val = None
            join_msg      = f"check error: {e}"

        def fmt_bool(val, true_label, false_label):
            if val is True:  return true_label
            if val is False: return false_label
            return "Unknown"

        cracked_str   = fmt_bool(cracked_val,   "✅ YES (offline)", "❌ NO (online-mode)")
        whitelist_str = fmt_bool(whitelist_val,  "❌ YES (blocked)",  "✅ NO (open)")

        if join_msg.startswith("Cracked ✅ | Plugin gate: "):
            disconnect_str = join_msg[len("Cracked ✅ | Plugin gate: "):]
        elif join_msg.startswith("Cracked ✅ | Kicked: "):
            disconnect_str = join_msg[len("Cracked ✅ | Kicked: "):]
        else:
            disconnect_str = "N/A"

        if "bad packet format" in join_msg or "error:" in join_msg:
            advisory_str = (
                "This server runs an outdated or modded protocol version that our probe "
                "couldn't handshake with correctly. Cracked/whitelist status could not be "
                "determined — the server may still be joinable manually."
            )
        else:
            advisory_str = None

        if players > 0:
            try:
                with counter_lock:
                    with_players += 1
                set_title()
            except Exception:
                pass

            gui_print(f"[ONLINE] {ip} {players}/{maxp} {version} | {join_msg}", "online")

            embed = build_active_embed(
                ip, config.PORT, players, maxp, version, motd, data,
                cracked_str, whitelist_str, disconnect_str, join_msg, advisory_str
            )

            key = f"{ip}:{config.PORT}"
            try:
                if await mark_sent(key):
                    asyncio.create_task(webhook(embed))
                    update_server(ip, config.PORT, motd, version, players, maxp, "", "")
                    with counter_lock:
                        sent_count += 1
                    gui_print(f"[WEBHOOK] queued", "webhook")
                else:
                    gui_print(f"[SKIP] {key} already sent", "webhook")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                gui_print(f"[SKIP] {key} error: {e}", "error")

        else:
            gui_print(f"[EMPTY] {ip} 0/{maxp} {version} | {join_msg}", "empty")

            empty_embed = build_empty_embed(
                ip, config.PORT, maxp, version, motd, data,
                cracked_str, whitelist_str, disconnect_str, join_msg, advisory_str
            )

            key = f"{ip}:{config.PORT}"
            try:
                if await mark_sent(key):
                    asyncio.create_task(webhook(empty_embed))
                    update_server(ip, config.PORT, motd, version, 0, maxp, "", "")
                    with counter_lock:
                        sent_count += 1
                    gui_print(f"[WEBHOOK] queued (empty)", "webhook")
                else:
                    gui_print(f"[SKIP] {key} already sent", "webhook")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                gui_print(f"[SKIP] {key} error: {e}", "error")

        if is_worker_mode:
            try:
                with worker_stats_lock:
                    worker_local_stats["scanned"] = scanned
                    worker_local_stats["found"] = found
                    worker_local_stats["with_players"] = with_players
                    worker_local_stats["sent_count"] = sent_count
            except Exception:
                pass


# ========= SCANNER RUN =========
async def run_scanner_instance(sem, instance_num, total_runs):
    global current_run, runs_completed

    ips_per_run = 1000

    try:
        gui_print(f"\n>>> STARTING RUN {instance_num}/{total_runs} <<<\n", "scan")
    except Exception:
        pass

    tasks = []
    scanned_in_run = 0

    while scanned_in_run < ips_per_run:
        if stop_event.is_set():
            break
        try:
            tasks.append(asyncio.create_task(scan(random_ip(), sem)))
            scanned_in_run += 1
        except Exception:
            continue

        if len(tasks) >= config.CONCURRENCY * 2:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            tasks.clear()

    if tasks:
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        tasks.clear()

    try:
        runs_completed += 1
        gui_print(f"\n>>> RUN {instance_num}/{total_runs} COMPLETED <<<\n", "online")
        if instance_num < total_runs:
            current_run = instance_num + 1
            gui_print(f"Preparing run {current_run}/{total_runs}...\n", "scan")
    except Exception:
        pass


# ========= WORKER MODE MAIN =========
async def worker_main():
    global scanned, found, with_players, sent_count, is_worker_mode

    is_worker_mode = True
    sem = asyncio.Semaphore(config.CONCURRENCY)

    print(f"[WORKER] Started worker instance (ID: {instance_mgr.instance_id})")
    print("[WORKER] Connecting to master...")

    if not instance_mgr.start_as_worker():
        print("[WORKER] Failed to connect to master, exiting")
        return

    print("[WORKER] Connected to master, starting scan...")

    async def report_stats():
        while True:
            try:
                await asyncio.sleep(2)
                with worker_stats_lock:
                    scans_per_min = compute_scans_per_minute(60)
                    found_per_min = compute_found_per_minute(60)
                    instance_mgr.send_worker_stats(
                        worker_local_stats["scanned"],
                        worker_local_stats["found"],
                        worker_local_stats["with_players"],
                        worker_local_stats["sent_count"],
                        peak_scans_per_minute=peak_scans_per_minute,
                        peak_found_per_minute=peak_found_per_minute,
                        scans_per_minute=scans_per_min,
                        found_per_minute=found_per_min
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WORKER] Stats reporting error: {e}")
                await asyncio.sleep(5)

    async def scanner_loop():
        tasks = []
        while True:
            if stop_event.is_set():
                break
            try:
                tasks.append(asyncio.create_task(scan(random_ip(), sem)))
                if len(tasks) >= config.CONCURRENCY * 2:
                    await asyncio.gather(*tasks, return_exceptions=True)
                    tasks.clear()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WORKER] Scanner loop error: {e}")
                await asyncio.sleep(1)

    try:
        await asyncio.gather(scanner_loop(), report_stats())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"[WORKER] Main loop error: {e}")
    finally:
        instance_mgr.disconnect_worker()
        print("[WORKER] Disconnected from master")


# ========= MAIN =========
async def main():
    global current_run, target_runs, is_worker_mode

    is_master = instance_mgr.check_master()

    if not is_master:
        is_worker_mode = True
        await worker_main()
        return

    is_worker_mode = False
    try:
        instance_mgr.start_as_master(on_worker_stats_received, on_worker_disconnect, on_server_broadcast)
        gui_print("[MASTER] Started as master instance", "scan")
        gui_print("[MASTER] Workers can now connect to this instance", "scan")
    except Exception as e:
        print(f"[MASTER] Failed to start as master: {e}")
        return

    sem = asyncio.Semaphore(config.CONCURRENCY)
    gui_print("=== MINECRAFT SERVER SCANNER STARTED ===", "scan")
    gui_print("Enter 'run 2-10' in CONNECT field for multi-run mode", "scan")
    gui_print("Standard mode: infinite scan\n", "scan")

    await asyncio.sleep(0.5)

    if target_runs >= 2:
        for run_num in range(1, target_runs + 1):
            if stop_event.is_set():
                break
            try:
                await run_scanner_instance(sem, run_num, target_runs)
            except asyncio.CancelledError:
                break
            except Exception as e:
                gui_print(f"[ERROR] Run {run_num} failed: {e}", "error")
                continue

        try:
            gui_print(f"\n=== ALL {target_runs} RUNS COMPLETED ===", "online")
            gui_print("Total servers scanned: " + str(scanned), "online")
            gui_print("Total servers found: " + str(found), "online")
            gui_print("Total with players: " + str(with_players), "online")
        except Exception:
            pass

        while not stop_event.is_set():
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
    else:
        tasks = []
        while True:
            if stop_event.is_set():
                break
            try:
                tasks.append(asyncio.create_task(scan(random_ip(), sem)))
                if len(tasks) >= config.CONCURRENCY * 2:
                    await asyncio.gather(*tasks, return_exceptions=True)
                    tasks.clear()
            except asyncio.CancelledError:
                break
            except Exception as e:
                gui_print(f"[ERROR] Scanner error: {e}", "error")
                continue


if __name__ == "__main__":
    is_master = instance_mgr.check_master()

    if not is_master:
        try:
            print("[WORKER] Starting in worker mode (no GUI)")
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\n[WORKER] Exiting...")
        finally:
            instance_mgr.stop()
    else:
        try:
            if tk is not None:
                gui_thread = threading.Thread(target=run_main_gui, daemon=True)
                gui_thread.start()
                time.sleep(1)
                asyncio.run(main())
            else:
                print("[ERROR] tkinter not available")
        except KeyboardInterrupt:
            print("\nExiting...")
        finally:
            instance_mgr.stop()