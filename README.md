# 🌹 CYBER MCS SCANNER v2.6

<div align="center">

![Version](https://img.shields.io/badge/Version-2.6-neonpink?style=for-the-badge&color=ff00aa)
![Python](https://img.shields.io/badge/Python-3.8+-blue?style=for-the-badge&color=00ffea)
![License](https://img.shields.io/badge/License-MIT-purple?style=for-the-badge&color=8a2be2)

**High-Performance Minecraft Server Scanner with Cyberpunk GUI**

[Features](#-features) • [Installation](#-installation) • [Configuration](#-configuration) • [Usage](#-usage) • [Multi-Instance](#-multi-instance-support)

</div>

---

## ✨ Features

### 🔍 High-Performance Scanning
- **Asynchronous Architecture** - Uses `asyncio` for maximum performance
- **High Concurrency** - Up to 500+ simultaneous connections
- **Intelligent IP Generation** - ASN-based and random IP ranges
- **Worldwide Coverage** - Covers all continents and major cloud providers

### 🎨 Cyberpunk GUI
- **Neon-colored Interface** - Pink, Purple and Cyan accents
- **Animated Elements** - Pulsating rose animation in the title
- **Tabs for Easy Navigation:**
  - ⚡ **Scanner** - Live scan log and statistics
  - 📈 **Advanced** - Real-time performance metrics with 10-second graph
  - ⚙️ **Settings** - Configuration of all parameters
  - 🆕 **Changelog** - Version history
  - 💜 **Credits** - ASCII art and developer info

### 📊 Real-time Statistics
- **Live Performance Tracking:**
  - Scanned servers
  - Found servers
  - Servers with players
  - Webhooks sent
  - Scans per hour
- **Advanced Stats:**
  - Scans per minute
  - Found servers per minute
  - Current scan rate (scans/second)
  - Peak performance tracking
  - 10-second history graph

### 🔔 Discord Integration
- **Automatic Webhook Notifications**
- **Rich Embeds** with server information:
  - Player count (online/max)
  - Minecraft version
  - MOTD (Message of the Day)
  - Color-coded (Green for online, Orange for empty)

### 🚀 Multi-Instance Support
- **Master/Worker Architecture**
- **Distributed Scanning** across multiple instances
- **Automatic Synchronization** of statistics
- **De-duplication** - No duplicate webhook notifications

---

## 📋 Prerequisites

- Python 3.8 or higher
- Windows, Linux or macOS
- Internet connection

---

## 🚀 Installation

### 1. Clone Repository

```bash
git clone https://github.com/cybermcs/scanner
cd cyber-mcs-scanner
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

**Required Packages:**
- `aiohttp` - Asynchronous HTTP requests
- `colorama` - Colored console output

Or simply:
```bash
pip install aiohttp colorama
```

### 3. Adjust Configuration

Edit `config/config.py`:

```python
WEBHOOK_URL = "https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"
PORT = 25565              # Standard Minecraft Port
TIMEOUT = 3               # Timeout in seconds
CONCURRENCY = 500         # Simultaneous connections
WEB_HOST = "0.0.0.0"      # Webserver host (for Multi-Instance)
WEB_PORT = 8080           # Webserver port (for Multi-Instance)
```

---

## 🎮 Usage

### Standard Mode (with GUI)

```bash
python scanner_v2GUI.py
```

The scanner starts with the cyberpunk GUI and begins scanning immediately.

### Multi-Run Mode

In the **CONNECT** field at the top right, you can enter:
- `run 2` to `run 10` - Runs 2-10 scan passes sequentially

Example:
```
run 5
```
Runs 5 passes with 1000 IPs each.

### Multi-Instance Mode

#### Start Master (first instance):
```bash
python scanner_v2GUI.py
```
The first instance automatically becomes the Master.

#### Start Workers (additional instances):
Simply start `scanner_v2GUI.py` in new terminal windows:
```bash
python scanner_v2GUI.py  # Instance 2 - automatically becomes Worker
python scanner_v2GUI.py  # Instance 3 - automatically becomes Worker
```

**Features in Multi-Instance Mode:**
- Automatic Master/Worker detection
- Statistics are aggregated and displayed in the Master
- No duplicate webhook notifications
- Workers can be started/stopped at any time

---

## ⚙️ Configuration

### Settings in the GUI

Under the **⚙️ SETTINGS** tab, you can adjust the following parameters:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `WEBHOOK_URL` | Discord Webhook URL | - |
| `PORT` | Minecraft Server Port | 25565 |
| `TIMEOUT` | Connection timeout (seconds) | 3 |
| `CONCURRENCY` | Simultaneous connections | 500 |
| `WEB_HOST` | Webserver host | 0.0.0.0 |
| `WEB_PORT` | Webserver port | 8080 |

**Note:** A restart is required after saving settings.

### Advanced Configuration

Additional parameters can be set in `config/config.py`:

```python
# Probability for ASN-based IPs (0.0 - 1.0)
ASN_PROB = 0.5

# CIDR expansion for ASN ranges (0-8)
ASN_EXPAND_BITS = 4

# Title update limits
TITLE_MIN_SECONDS = 0.5
TITLE_SCAN_STEP = 10
```

---

## 📊 Statistics Explained

### Main Statistics (Scanner Tab)
- **Scanned** - Number of scanned IPs
- **Found** - Number of found Minecraft servers
- **With Players** - Servers with at least 1 player
- **Server scanner per hour** - Estimated scan rate per hour
- **Webhooks Sent** - Number of sent Discord notifications
- **Active Scanners** - Number of active scanner instances
- **Run Progress** - Progress in Multi-Run mode

### Advanced Statistics (Advanced Tab)
- **Scans/Min** - Average scans per minute
- **Found/Min** - Average found servers per minute
- **Current Rate** - Current scan rate (scans/second)
- **Peak Scans/Min** - Highest scan rate ever achieved
- **10-Second Graph** - Visualization of the last 10 seconds

---

## 🏗️ Project Structure

```
cyber-mcs-scanner/
├── 📁 ascii/
│   └── ascii_art.txt          # ASCII art for Credits
├── 📁 beta/                   # Beta features and experiments
│   ├── botv1.py
│   └── whitelist/             # Whitelist scanner
├── 📁 config/
│   └── config.py              # Main configuration
├── 📁 outdated/               # Old versions
│   ├── scanner.py
│   ├── scanner_v2.py
│   └── mcs_multi_tool.py
├── 📁 ressources/
│   ├── instance_manager.py    # Multi-Instance management
│   ├── rose.ico              # Icon file
│   └── sent_servers.txt      # Persistent sent list
├── scanner_v2GUI.py          # Main application (GUI)
├── setup.bat                 # Windows setup script
├── requirements.txt          # Python dependencies
└── README.md                 # This file
```

---

## 🔧 Technical Details

### Scanning Algorithm
1. **IP Generation** - Random IPs from ASN ranges or completely random
2. **Minecraft Handshake** - Establish protocol-compliant connection
3. **Status Query** - Retrieve server information
4. **Processing** - Parse and display data
5. **Webhook** - Notify Discord when servers are found

### ASN Ranges
The scanner uses IP ranges from major hosting providers:
- **Hetzner** (Germany)
- **OVH** (Europe & USA)
- **DigitalOcean**
- **Contabo**
- **Netcup**
- **AWS, Azure, Google Cloud**
- **Linode, Vultr**
- **And many more...**

### Performance Optimizations
- **ThreadPoolExecutor** for CPU-intensive tasks
- **AsyncIO Semaphore** for controlled concurrency
- **Connection Pooling** for HTTP sessions
- **Efficient Data Structures** (deque, sets)
- **Thread-safe Counters** with locks

---

## 🐛 Troubleshooting

### GUI doesn't start
```bash
# Check if tkinter is installed
python -c "import tkinter; print(tkinter.Tcl().eval('info patchlevel'))"
```

### Webhook doesn't work
- Check the webhook URL in `config/config.py`
- Make sure the URL starts with `https://discord.com/api/webhooks/`
- Check the console for error messages

### Too few servers found
- Increase `CONCURRENCY` in the configuration
- Decrease `TIMEOUT` for faster scanning
- Make sure your internet connection is stable

### Multi-Instance doesn't work
- Make sure `WEB_PORT` (default: 8080) is not blocked
- Check firewall settings
- Each instance must run on the same host (or have network connectivity)

---

## 📝 Changelog

### v2.6 "Neon Nights" (2026-02-14)
- 🌟 Advanced Stats Tab with real-time performance metrics
- 🌟 10-second scan history graph
- 🌟 Changelog and Credits tabs
- 🔧 Improved performance and stability
- 🚀 Better Master/Worker merging

### v2.5 "Cyberpunk Edition" (2026-02-13)
- 🎨 Complete GUI overhaul with cyberpunk theme
- 🚀 Improved scanning performance
- 🔧 Various bugfixes

### v2.4 "First GUI Release" (2026-02-12)
- 🎉 First GUI version
- ⚡ High-Performance Minecraft Server Scanner

---

## 💜 Credits

**Developers:**
- 🌹 **n3xtgen** (aka EliasPython)
- 🐍 **Proxyshart** (aka meowinc-owner)

**Special Thanks:**
- Minecraft Community for protocol reverse-engineering
- All testers and contributors

---

## 📸 Screenshots

You can add screenshots to showcase your project:

### Method 1: Direct Image Upload (GitHub)
1. Go to your repository on GitHub
2. Navigate to **Issues** → **New Issue**
3. Drag & drop your images into the issue text box
4. GitHub will generate a URL like: `https://user-images.githubusercontent.com/...`
5. Copy that URL and paste it into your README

### Method 2: Images in Repository
Add images to a `screenshots/` folder and reference them:

```markdown
![Scanner GUI](screenshots/gui.png)
![Advanced Stats](screenshots/advanced.png)
```

### Method 3: External Hosting
Use image hosting services like Imgur:

```markdown
![Description](https://i.imgur.com/your-image-id.png)
```

### Example Screenshot Placeholders:

<div align="center">

| Scanner Tab | Advanced Stats |
|-------------|----------------|
| ![Scanner](https://via.placeholder.com/400x300/000000/ff00aa?text=Scanner+GUI) | ![Advanced](https://via.placeholder.com/400x300/000000/8a2be2?text=Advanced+Stats) |

</div>

---

## 📄 License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

<div align="center">


**Made with 💜 and 🐍**

🌹 *Cyber MCS Scanner - Scan the world, find the servers* 🌹

</div>
