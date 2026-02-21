# Cyber MCS Scanner

**Version 2.6** | Python 3.8+ | MIT License

A high-performance, asynchronous Minecraft server scanner with a cyberpunk-themed graphical interface and Discord webhook integration.

---

## Overview

Cyber MCS Scanner discovers publicly accessible Minecraft servers by scanning IP ranges at high throughput. It combines an asynchronous scanning engine with a real-time GUI, Discord notifications, and a distributed multi-instance architecture for coordinated large-scale scanning.

---

## Features

### Scanning Engine

- Asynchronous architecture via `asyncio` for high-throughput, non-blocking I/O
- Up to 500 simultaneous connections with semaphore-controlled concurrency
- Two IP generation strategies: ASN-targeted ranges and fully random generation
- Worldwide coverage including major cloud providers and hosting networks

### Graphical Interface

The GUI uses a cyberpunk color palette (neon pink, purple, and cyan) and is organized into five tabs:

| Tab | Purpose |
|-----|---------|
| Scanner | Live scan log and primary statistics |
| Advanced | Real-time performance metrics with a 10-second history graph |
| Settings | Configuration of all runtime parameters |
| Changelog | Version history |
| Credits | Developer information |

### Statistics and Monitoring

The scanner tracks the following metrics in real time:

- Total IPs scanned
- Minecraft servers found
- Servers with active players
- Discord webhooks dispatched
- Scans per hour, per minute, and per second
- Peak scan rate
- Active scanner instances

### Discord Integration

When a server is discovered, a formatted webhook embed is sent to your configured Discord channel containing the player count, Minecraft version, MOTD, and a color indicator (green for servers with players, orange for empty servers).

### Multi-Instance Support

Multiple scanner instances can run concurrently using a Master/Worker architecture. The first instance launched becomes the Master and aggregates statistics from all Workers. Deduplication logic prevents duplicate webhook notifications across instances.

---

## Requirements

- Python 3.8 or higher
- Windows, Linux, or macOS
- Active internet connection

---

## Installation

**1. Clone the repository**

```bash
git clone https://github.com/cybermcs/scanner
cd cyber-mcs-scanner
```

**2. Install dependencies**

```bash
pip install -r requirements.txt
```

Required packages: `aiohttp`, `colorama`

**3. Configure the scanner**

Edit `config/config.py` before first launch:

```python
WEBHOOK_URL  = "https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"
PORT         = 25565   # Standard Minecraft port
TIMEOUT      = 3       # Connection timeout in seconds
CONCURRENCY  = 500     # Maximum simultaneous connections
WEB_HOST     = "0.0.0.0"
WEB_PORT     = 8080
```

---

## Usage

### Standard Mode

```bash
python scanner_v2GUI.py
```

The GUI launches and scanning begins immediately.

### Multi-Run Mode

In the **CONNECT** field (top right of the GUI), enter a run count to execute sequential scan passes:

```
run 5
```

This runs 5 passes of 1,000 IPs each.

### Multi-Instance Mode

Start the first instance normally -- it becomes the Master automatically:

```bash
python scanner_v2GUI.py
```

Launch additional instances in separate terminal windows. Each subsequent instance connects to the Master as a Worker:

```bash
python scanner_v2GUI.py   # Worker 2
python scanner_v2GUI.py   # Worker 3
```

Workers can be started and stopped at any time. The Master displays aggregated statistics for all active instances.

---

## Configuration Reference

### GUI Settings (Settings Tab)

| Parameter | Description | Default |
|-----------|-------------|---------|
| `WEBHOOK_URL` | Discord webhook endpoint | None |
| `PORT` | Minecraft server port to scan | 25565 |
| `TIMEOUT` | Per-connection timeout (seconds) | 3 |
| `CONCURRENCY` | Simultaneous connection limit | 500 |
| `WEB_HOST` | Multi-instance web server host | 0.0.0.0 |
| `WEB_PORT` | Multi-instance web server port | 8080 |

A restart is required after saving settings.

### Advanced Parameters (`config/config.py`)

| Parameter | Description | Default |
|-----------|-------------|---------|
| `ASN_PROB` | Probability of ASN-targeted IP generation (0.0 to 1.0) | 0.5 |
| `ASN_EXPAND_BITS` | CIDR expansion for ASN ranges (0 to 8) | 4 |
| `TITLE_MIN_SECONDS` | Minimum interval for title updates | 0.5 |
| `TITLE_SCAN_STEP` | Scan count increment per title update | 10 |

---

## Technical Details

### Scan Sequence

1. **IP Generation** -- Produce candidate IPs from ASN ranges or random address space
2. **Handshake** -- Initiate a protocol-compliant Minecraft connection
3. **Status Query** -- Request server metadata over the established connection
4. **Parsing** -- Extract and display player count, version, and MOTD
5. **Notification** -- Send a Discord webhook embed if a server is found

### Targeted ASN Ranges

The scanner prioritizes IP ranges belonging to hosting providers where Minecraft servers are commonly deployed, including:

Hetzner, OVH, DigitalOcean, Contabo, Netcup, Linode, Vultr, AWS, Azure, Google Cloud, and additional regional providers.

### Performance Architecture

- `ThreadPoolExecutor` for CPU-bound parsing tasks
- `asyncio.Semaphore` for controlled concurrency
- HTTP connection pooling via `aiohttp`
- Thread-safe counters with `threading.Lock`
- `deque` and `set` for efficient state management

---

## Project Structure

```
cyber-mcs-scanner/
  ascii/
    ascii_art.txt              ASCII art for the Credits tab
  beta/
    botv1.py
    whitelist/                 Experimental whitelist scanner
  config/
    config.py                  Primary configuration file
  outdated/
    scanner.py
    scanner_v2.py
    mcs_multi_tool.py
  ressources/
    instance_manager.py        Multi-instance coordination
    rose.ico                   Application icon
    sent_servers.txt           Persistent deduplication list
  scanner_v2GUI.py             Main application entry point
  setup.bat                    Windows setup script
  requirements.txt             Python dependencies
  README.md
```

---

## Troubleshooting

**The GUI does not launch**

Verify that `tkinter` is available in your Python installation:

```bash
python -c "import tkinter; print(tkinter.Tcl().eval('info patchlevel'))"
```

**Webhooks are not being sent**

Confirm that `WEBHOOK_URL` in `config/config.py` is correct and begins with `https://discord.com/api/webhooks/`. Check the console output for HTTP error codes.

**Very few servers are being found**

Increase `CONCURRENCY` or decrease `TIMEOUT` in the configuration. Ensure your network connection is stable and not subject to outbound rate limiting.

**Multi-instance mode is not synchronizing**

Verify that port `8080` (or your configured `WEB_PORT`) is not blocked by a firewall. All instances must be able to reach each other over the network, or run on the same host.

---

## Changelog

### v2.6 "Neon Nights" -- 2026-02-14
- Added Advanced Stats tab with real-time performance metrics
- Added 10-second scan history graph
- Added Changelog and Credits tabs
- Improved Master/Worker merging logic
- General performance and stability improvements

### v2.5 "Cyberpunk Edition" -- 2026-02-13
- Complete GUI redesign with cyberpunk theme
- Improved scanning throughput
- Various bug fixes

### v2.4 "First GUI Release" -- 2026-02-12
- Initial GUI release
- High-performance asynchronous scanner core

---

## Credits

Developed by **n3xtgen** (EliasPython) and **Proxyshart** (meowinc-owner).

Thanks to the Minecraft community for protocol documentation and reverse-engineering efforts, and to all contributors and testers.

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
