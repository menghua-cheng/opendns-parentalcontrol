# OpenDNS Parental Control Automation

**Automate OpenDNS content filtering via Python & Selenium**

A cross-platform tool (Windows, Linux, WSL) that logs into your OpenDNS dashboard and toggles parental-control categories with a single command. Perfect for network security, IT admins, and parents.

---

## 🚀 Features

- **Automated Content Filtering**: Toggle allow/block categories like *Video Sharing*, *Social Networking*, *Adult Themes*.
- **Headless Browser**: Runs in the background with Selenium & ChromeDriver or GeckoDriver.
- **Config Overrides**: INI file (`opendns.conf`) + environment variables for flexible setup.
- **Auto Backups**: Saves timestamped config snapshots (`opendns.conf.YYYYMMDDHHMMSS`).
- **Logging & Debug**: Detailed console/file logs, optional screenshots & HTML dumps.

## 🛠️ Installation

```bash
git clone https://github.com/menghua-cheng/opendns-parentalcontrol.git
cd opendns-parentalcontrol
pip install selenium webdriver-manager
```

### Ubuntu / WSL Dependencies
```bash
sudo apt update && sudo apt install -y python3-pip chromium-browser chromium-chromedriver firefox geckodriver
```

## ⚙️ Configuration

1. Copy `opendns.conf.sample` → `opendns.conf`.
2. Set your OpenDNS credentials, `NETWORK_ID`, `CATEGORIES`, and optional `SCREENSHOT_PATH`.
3. (Optional) Override in env vars: `OPENDNS_USER`, `OPENDNS_PASS`, `NETWORK_ID`, `CATEGORIES`, `BROWSER`.

```ini
[opendns]
OPENDNS_USER = your_email@example.com
OPENDNS_PASS = your_password
NETWORK_ID   = 123456789
CATEGORIES   = Social Networking, Video Sharing
```

## ▶️ Usage

```bash
# Block configured categories
python opendns_parental_control.py --off

# Allow configured categories
python opendns_parental_control.py --on

# List categories
python opendns_parental_control.py --list-categories

# Save current settings
python opendns_parental_control.py --login-save-current

# Apply a custom config (e.g., for kids' bedtime)
python opendns_parental_control.py --apply kids-bedtime.conf
```

Add `--debug` for screenshots & HTML dumps, and `--headless=false` to watch the browser.

## 📈 SEO & Keywords
- OpenDNS parental control automation
- Python Selenium OpenDNS script
- Network security filtering tool
- Automated internet content filtering script

---

**License**: MIT  
**Author**: menghua © 2025
