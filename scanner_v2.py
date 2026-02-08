import asyncio, random, socket, struct, json, aiohttp, os, sys, time
from colorama import Fore, Style, init
import config.config as config
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=config.CONCURRENCY)

http_session: aiohttp.ClientSession | None = None

last_title_update = 0
last_title_scan_count = 0

# Title update thresholds (can be overridden in config.py)
TITLE_MIN_SECONDS = getattr(config, 'TITLE_MIN_SECONDS', 0.5)
TITLE_SCAN_STEP = getattr(config, 'TITLE_SCAN_STEP', 10)


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

# ========= SENT PERSISTENCE =========
SENT_FILE = "sent_servers.txt"
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
    """Mark key as sent. Returns True if newly marked, False if already present."""
    global sent_set
    async with sent_lock:
        if key in sent_set:
            return False
        sent_set.add(key)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _append_sent_file, key)
    return True

# load existing sent entries
load_sent()


# ========= STARTUP ASCII + LOADING =========
def load_ascii_art_file(path: str = "ascii\\ascii_art.txt") -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return (
 
        )


def show_startup(duration: float = 10.0):
    art = load_ascii_art_file()
    print(Style.BRIGHT + Pink + art)

    steps = 40
    step_delay = duration / steps
    try:
        for i in range(steps + 1):
            pct = int(i * 100 / steps)
            bar = ("#" * int(i * 30 / steps)).ljust(30)
            # print loading line with a pink->magenta->red gradient (bias increases red area)
            line = f"Loading: [{bar}] {pct}%"
            print(gradient_text(line, PINK_GRAD, bias=0.7), end="\r", flush=True)
            time.sleep(step_delay)
    except KeyboardInterrupt:
        pass
    # clear the startup screen before main
    if os.name == "nt":
        os.system("cls")
    else:
        os.system("clear")


def set_console_size(cols: int = 120, lines: int = 40):
    """Attempt to resize the terminal window on startup (Windows only)."""
    try:
        if os.name == "nt":
            # Use 'mode' to set console size on Windows
            os.system(f"mode con: cols={cols} lines={lines}")
    except Exception:
        pass

# ========= COUNTER =========
scanned = 0
found = 0
with_players = 0

# ========= TITLE =========

def set_title():
    global last_title_update
    global last_title_scan_count
    now = time.time()
    # Only update if enough time has passed OR enough scans have occurred
    time_ok = (now - last_title_update) >= TITLE_MIN_SECONDS
    scans_ok = (scanned - last_title_scan_count) >= TITLE_SCAN_STEP
    if not (time_ok or scans_ok):
        return
    last_title_update = now
    last_title_scan_count = scanned
    title_text = f"Scanned: {scanned} ^| Servers: {found} ^| With Players: {with_players}"
    if os.name == "nt":
        os.system(f"title {title_text}")
    else:
        try:
            sys.stdout.write(f"\x1b]0;{title_text}\x07")
            sys.stdout.flush()
        except Exception:
            pass


# ================================ IP =================================


# ========= ASN RANGES =========
ASN_RANGES = [
    # Hetzner (DE)
    ("88.198.0.0", 16),
    ("95.216.0.0", 15),
    ("116.202.0.0", 16),
    ("138.201.0.0", 16),
    ("159.69.0.0", 16),

    # OVH (EU)
    ("51.38.0.0", 16),
    ("54.36.0.0", 16),
    ("145.239.0.0", 16),

    # OVH US
    ("137.74.0.0", 16),

    # DigitalOceans
    ("142.93.0.0", 16),
    ("159.65.0.0", 16),
    ("167.99.0.0", 16),

    # Contabo
    ("5.189.0.0", 16),
    ("37.228.0.0", 16),
    ("185.228.0.0", 16),

    # Netcup
    ("89.58.0.0", 16),
    ("46.38.0.0", 16),

    # AWS
    ("18.0.0.0", 8),
    ("3.0.0.0", 8),

    # Azure
    ("20.0.0.0", 8),

    # Google Clouds
    ("34.0.0.0", 8),
]

# Configure how IPs are selected. Lower `ASN_PROB` => more full-random IPs.
ASN_PROB = getattr(config, 'ASN_PROB', 0.5)
# Expand CIDR masks by this many bits when sampling from ASN ranges (0 = no expansion).
ASN_EXPAND_BITS = getattr(config, 'ASN_EXPAND_BITS', 4)

# Additional ASN ranges to increase coverage
ASN_RANGES += [
    ("162.243.0.0", 16),  # Linode
    ("198.199.0.0", 16),  # DigitalOcean
    ("104.248.0.0", 16),  # Vultr
    ("207.148.0.0", 16),  # Vultr
    ("138.68.0.0", 16),   # DigitalOcean
    ("165.227.0.0", 16),  # DigitalOcean / Linode
    ("157.230.0.0", 16),  # DigitalOcean
    ("104.236.0.0", 16),  # DigitalOcean
    ("45.55.0.0", 16),    # DigitalOcean
    ("64.62.0.0", 16),    # Linode
    ("45.79.0.0", 16),    # Vultr
    ("149.56.0.0", 16),   # Scaleway / misc
    ("192.241.128.0", 17),
    ("185.117.0.0", 16),
    ("213.32.0.0", 16),
    ("46.105.0.0", 16),
    ("185.104.0.0", 16),
    ("91.121.0.0", 16),
    ("185.6.0.0", 16),
]

# --- Larger continental coverage (additional plausible blocks per continent) ---
CONTINENTAL_RANGES = [
    # Europe (various providers)
    ("5.39.0.0", 16),
    ("31.13.0.0", 16),
    ("46.101.0.0", 16),
    ("51.15.0.0", 16),
    ("62.75.0.0", 16),
    ("77.73.0.0", 16),
    ("80.67.0.0", 16),

    # North America (clouds / hosting)
    ("104.0.0.0", 16),
    ("107.170.0.0", 16),
    ("173.194.0.0", 16),
    ("74.125.0.0", 16),
    ("96.0.0.0", 16),

    # Asia
    ("103.4.0.0", 16),
    ("116.31.0.0", 16),
    ("119.28.0.0", 16),
    ("123.125.0.0", 16),

    # South America
    ("177.53.0.0", 16),
    ("179.43.0.0", 16),
    ("181.224.0.0", 16),

    # Africa
    ("41.0.0.0", 16),
    ("102.66.0.0", 16),
    ("154.0.0.0", 16),

    # Oceania
    ("103.20.0.0", 16),
    ("203.0.0.0", 16),
    ("1.0.0.0", 16),

    # Misc / regional providers
    ("185.8.0.0", 16),
    ("185.9.0.0", 16),
    ("178.62.0.0", 16),
    ("159.203.0.0", 16),
    ("157.230.0.0", 16),
]

ASN_RANGES += CONTINENTAL_RANGES

# ========= IP UTILS =========
def ip_to_int(ip):
    a, b, c, d = map(int, ip.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d

def int_to_ip(i):
    return ".".join(str((i >> s) & 255) for s in (24, 16, 8, 0))

def random_from_cidr(base, mask, expand_bits: int = 0):
    """Pick a random IP inside `base/mask`, optionally expanding the mask by
    `expand_bits` (smaller mask => larger block). Expansion stops at /8.
    """
    base_int = ip_to_int(base)
    new_mask = max(8, mask - expand_bits)
    host_bits = 32 - new_mask
    rand = random.randint(1, (1 << host_bits) - 2)
    return int_to_ip(base_int + rand)

# ========= FINAL GENERATOR =========
def random_ip():
    # ASN vs full-random selection is configurable via ASN_PROB
    if random.random() < ASN_PROB:
        base, mask = random.choice(ASN_RANGES)
        return random_from_cidr(base, mask, ASN_EXPAND_BITS)

    # fallback = public random
    while True:
        a = random.randint(1, 223)
        b = random.randint(0, 255)
        c = random.randint(0, 255)
        d = random.randint(1, 254)

        if a in (10, 127, 0):
            continue
        if a == 169 and b == 254:
            continue
        if a == 172 and 16 <= b <= 31:
            continue
        if a == 192 and b == 168:
            continue
        if a >= 224:
            continue
        if a == 100 and 64 <= b <= 127:
            continue

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


#========== PINK TEXT ==========
def pink(text):
    out = ""
    for i, c in enumerate(text):
        out += PINK[i % len(PINK)] + c
    return out + Style.RESET_ALL

# ========= RAINBOW TEXT =========
def rainbow(text):
    out = ""
    for i, c in enumerate(text):
        out += RAINBOW[i % len(RAINBOW)] + c
    return out + Style.RESET_ALL

# ========= ANIMATE COLORS =========
def animate_colors_line(text: str, cycles: int = 6, delay: float = 0.08):
    """Animate a single line by cycling character colors.

    - `text`: the text to animate
    - `cycles`: how many frames to show (increase for longer animation)
    - `delay`: seconds between frames
    """
    try:
        for frame in range(cycles):
            out = ""
            for i, ch in enumerate(text):
                color = RAINBOW[(i + frame) % len(RAINBOW)]
                out += color + ch
            print(Style.BRIGHT + out + Style.RESET_ALL, end="\r", flush=True)
            time.sleep(delay)
        # keep final state on its own line
        print()
    except KeyboardInterrupt:
        print()

# ========= GRADIENT TEXT =========
def gradient_text(text: str, colors: list | None = None, bias: float = 1.0) -> str:
    """Return text colored with a left-to-right gradient using `colors`.

    `colors` should be a list of color codes (from colorama.Fore). If None,
    `PINK_GRAD` is used.
    """
    if colors is None:
        colors = PINK_GRAD
    out = ""
    n = len(text)
    if n == 0:
        return ""
    for i, ch in enumerate(text):
        # map character position to a color index across the colors list
        t = i / max(n - 1, 1)
        # apply bias (<1 shifts earlier to later colors, >1 compresses)
        t = t ** bias
        idx = int(round(t * (len(colors) - 1)))
        out += colors[idx] + ch
    return out + Style.RESET_ALL


def animate_gradient_line(text: str, cycles: int = 8, delay: float = 0.06, colors: list | None = None):
    """Animate a gradient by rotating the provided colors across frames."""
    if colors is None:
        colors = PINK_GRAD
    try:
        for frame in range(cycles):
            # rotate colors so gradient appears to move
            rot = colors[frame % len(colors):] + colors[:frame % len(colors)]
            print(Style.BRIGHT + gradient_text(text, rot, bias=0.7) + Style.RESET_ALL, end="\r", flush=True)
            time.sleep(delay)
        print()
    except KeyboardInterrupt:
        print()

# ========= BLINK =========
def blink(text):
    print(Style.BRIGHT + text)
    time.sleep(0.05)
    print("\033[F\033[K", end="")
# ======== BEEP =========

def play_beep():
    
    try:
        if os.name == 'nt':
            try:
                import winsound

                winsound.Beep(1000, 200)
                return
            except Exception:
                pass

        # Fallback: terminal bell
        sys.stdout.write('\a')
        sys.stdout.flush()
    except Exception:
        pass

# ========= WEBHOOK =========
async def webhook(msg):
    global http_session
    if http_session is None:
        http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        )
    # Allow either a plain string message or a dict representing an embed
    payload = {}
    if isinstance(msg, dict):
        payload["embeds"] = [msg]
    else:
        payload["content"] = msg

    try:
        async with http_session.post(
            config.WEBHOOK_URL,
            json=payload
        ) as r:
            if r.status not in (200, 204):
                print(ERROR + f"[WEBHOOK ERROR] {r.status}")
    except Exception as e:
        print(ERROR + f"[WEBHOOK FAIL] {e}")
        
# ========= SCAN =========
async def scan(ip, sem):
    global scanned, found, with_players

    async with sem:
        scanned += 1
        set_title()
        print(SCAN + f"[SCAN] {ip}", flush=True)

        data = await asyncio.get_running_loop().run_in_executor(executor, ping, ip)

        if not data:
            print(NOSRV + f"[NONE] {ip}")
            return

        found += 1
        set_title()

        players = data["players"]["online"]
        maxp = data["players"]["max"]
        version = data["version"]["name"]
        motd = data["description"]
        if isinstance(motd, dict):
            motd = motd.get("text", "")

        if players > 0:
            with_players += 1
            set_title()

            text = f"[ONLINE] {ip} {players}/{maxp} {version}"
            blink(ONLINE + text)
            print(ONLINE + rainbow(text))

            # play beep to notify an online server
            try:
                play_beep()
            except Exception:
                pass

            # Build a Discord embed payload
            motd_text = motd or "-"
            if len(motd_text) > 1020:
                motd_text = motd_text[:1017] + "..."

            embed = {
                "title": "Minecraft Server Online",
                "description": f"{ip}:{config.PORT}",
                "color": 3066993,  # green
                "fields": [
                    {"name": "Spieler", "value": f"{players}/{maxp}", "inline": True},
                    {"name": "Version", "value": version, "inline": True},
                    {"name": "MOTD", "value": motd_text, "inline": False},
                ]
            }

            key = f"{ip}:{config.PORT}"
            if await mark_sent(key):
                await webhook(embed)
                print(WEBHOOK + f"[WEBHOOK] sent")
            else:
                print(WEBHOOK + f"[SKIP] {key} already sent")

        else:
            print(EMPTY + f"[EMPTY] {ip} 0/{maxp} {version}")

            # Also send an embed for empty servers
            motd_text = motd or "-"
            if len(motd_text) > 1020:
                motd_text = motd_text[:1017] + "..."

            empty_embed = {
                "title": "Minecraft Server Empty",
                "description": f"{ip}:{config.PORT}",
                "color": 15105570,  # orange
                "fields": [
                    {"name": "Spieler", "value": f"0/{maxp}", "inline": True},
                    {"name": "Version", "value": version, "inline": True},
                    {"name": "MOTD", "value": motd_text, "inline": False},
                ]
            }

            key = f"{ip}:{config.PORT}"
            if await mark_sent(key):
                await webhook(empty_embed)
                print(WEBHOOK + f"[WEBHOOK] sent (empty)")
            else:
                print(WEBHOOK + f"[SKIP] {key} already sent")

# ========= MAIN =========
async def main():
    sem = asyncio.Semaphore(config.CONCURRENCY)
    tasks = []
    print(rainbow("=== MINECRAFT SERVER SCANNER STARTED ==="))

    while True:
        tasks.append(asyncio.create_task(scan(random_ip(), sem)))
        if len(tasks) >= config.CONCURRENCY * 2:
            await asyncio.gather(*tasks)
            tasks.clear()

if __name__ == "__main__":
    # make the console a bit larger on start
    set_console_size(cols=120, lines=40)
    show_startup(10.0)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
