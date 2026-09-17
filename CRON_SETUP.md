# End-of-Day Cron Job Setup Guide

This guide explains how to schedule the daily end-of-day scraping job (`main.py ingest`) to automatically pull KBank transaction emails from Gmail.

---

## 1. Prerequisites for Headless Execution

Before setting up automated scheduling:

1. **Initial OAuth Authentication**:
   Run `main.py ingest` interactively once in a terminal.
   ```bash
   source .venv/bin/activate
   python main.py ingest
   ```
   This will prompt a browser window to grant Gmail read permissions and generate `./credentials/token.json`. Once generated, all subsequent runs will refresh the token headlessly without user intervention.
2. **Environment Configuration**:
   Ensure `.env` contains your `OPENAI_API_KEY` and any necessary path overrides.

---

## 2. Option A: Standard Crontab (macOS & Linux)

### Recommended Schedule
Run daily at 11:45 PM (23:45):

```cron
45 23 * * *
```

### Crontab Entry

1. Open your user crontab editor:
   ```bash
   crontab -e
   ```

2. Add the following line (replace `/Users/rampam/Documents/DeepAgent_Project` with your actual path):
   ```cron
   45 23 * * * cd /Users/rampam/Documents/DeepAgent_Project && .venv/bin/python main.py ingest >> logs/cron.log 2>&1
   ```

3. Create the log directory:
   ```bash
   mkdir -p /Users/rampam/Documents/DeepAgent_Project/logs
   ```

> **Note on macOS Full Disk Access**:
> On modern macOS versions (Ventura, Sonoma, Sequoia), `cron` requires Full Disk Access under **System Settings > Privacy & Security > Full Disk Access**. If you prefer a native macOS solution that doesn't suffer from sleep/wake issues, use **Option B (launchd)** below.

---

## 3. Option B: macOS Native `launchd` (Recommended for Mac Users)

`launchd` is macOS's native service manager. Unlike cron, `launchd` automatically runs missed jobs as soon as your Mac wakes up from sleep.

### 1. Create the LaunchAgent Plist File
Save the following file to `~/Library/LaunchAgents/com.rampam.financetracker.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.rampam.financetracker</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/rampam/Documents/DeepAgent_Project/.venv/bin/python</string>
        <string>/Users/rampam/Documents/DeepAgent_Project/main.py</string>
        <string>ingest</string>
        <string>--max-results</string>
        <string>50</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/rampam/Documents/DeepAgent_Project</string>

    <!-- Run every day at 23:45 -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>23</integer>
        <key>Minute</key>
        <integer>45</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/rampam/Documents/DeepAgent_Project/logs/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/rampam/Documents/DeepAgent_Project/logs/launchd_error.log</string>
</dict>
</plist>
```

### 2. Load and Activate the Agent
```bash
# Ensure log directory exists
mkdir -p /Users/rampam/Documents/DeepAgent_Project/logs

# Load the service
launchctl load ~/Library/LaunchAgents/com.rampam.financetracker.plist

# Check status
launchctl list | grep financetracker
```

### 3. Test Immediately
You can test the job without waiting until 23:45:
```bash
launchctl start com.rampam.financetracker
cat /Users/rampam/Documents/DeepAgent_Project/logs/launchd.log
```

---

## 4. Option C: Linux systemd Timer (For Cloud / Raspberry Pi / Server)

If hosting on a Linux server or homelab:

### Service File: `/etc/systemd/system/finance-tracker.service`
```ini
[Unit]
Description=KBank Gmail Finance Tracker Ingestion
After=network.target

[Service]
Type=oneshot
WorkingDirectory=/home/user/DeepAgent_Project
EnvironmentFile=/home/user/DeepAgent_Project/.env
ExecStart=/home/user/DeepAgent_Project/.venv/bin/python main.py ingest
StandardOutput=append:/home/user/DeepAgent_Project/logs/systemd.log
StandardError=append:/home/user/DeepAgent_Project/logs/systemd_error.log
```

### Timer File: `/etc/systemd/system/finance-tracker.timer`
```ini
[Unit]
Description=Run Finance Tracker nightly at 23:45

[Timer]
OnCalendar=*-*-* 23:45:00
Persistent=true

[Install]
WantedBy=timers.target
```

### Enable Timer:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now finance-tracker.timer
```

---

## 5. Monitoring & Maintenance

- **Check Ingestion Logs**:
  ```bash
  tail -n 50 logs/cron.log
  ```
- **Check for Items Needing Review**:
  ```bash
  python main.py review
  ```
- **On-Demand Report**:
  Generate your monthly summary at any time:
  ```bash
  python main.py report $(date +%Y-%m)
  ```
