# OYI for Android

Native Android companion app for OYI. It does three things:

1. **Overlay bubble** — a small always-on bubble that shows which app is open,
   how long you have been in it, the state of the OYI link, and today's total
   for that app.
2. **Usage tracking** — foreground app + duration history stored locally in
   SQLite (`UsageStatsManager`).
3. **Phone control** — an outbound WebSocket to the OYI gateway. The server
   asks `nvidia/Nemotron-3.5-Super-VL` what to do next; this app executes one
   action per step (tap, tap by text, type, scroll, back/home/recents, open
   app) through its accessibility service.

Target device: **itel City 100 (C671L), Android 14 / API 34**.

## Architecture

```
Android app (this folder)
  OyiService (foreground service)
    ├── OverlayController      bubble + panel
    ├── UsageTracker           UsageStatsManager -> UsageRepository (SQLite)
    └── DeviceClient           outbound WSS -> /ws/device
  OyiAccessibilityService
    ├── DeviceController       executes one UI action per step
    └── UiTreeSerializer       compact accessibility tree for the model
```

```
OYI server (repo root)
  webapp_server.py   /ws/device, /api/device/{health,devices,command,cancel}
  device_gateway.py  command state machine (command -> observation -> action)
  vision_agent.py    Nebius Nemotron-3.5-Super-VL planner
  device_tool.py     `control_phone` agent tool
  channels/          messaging adapters (Telegram first, more later)
```

The phone only ever dials out, so no inbound port, ADB, Shizuku or device-owner
setup is needed on the phone.

## Build

Requirements: JDK 17, Android SDK with platform 35.

```bash
cd android
./gradlew assembleDebug
# -> android/app/build/outputs/apk/debug/app-debug.apk
```

Optionally bake in the gateway URL:

```bash
./gradlew assembleDebug -POYI_DEFAULT_GATEWAY_URL=https://oyi.example
```

## Install and pair

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

1. Open **OYI** on the phone.
2. Grant the three permissions the app asks for:
   - **Display over other apps** (overlay bubble)
   - **Usage access** (foreground app + duration)
   - **Accessibility -> OYI device control** (phone control; required for the
     vision loop because it is what reads the screen and taps)
3. Paste the gateway URL (for example `https://150-230-152-243.sslip.io`) and
   the pairing token, then **Save**.
4. Turn on **OYI overlay bubble**.

The pairing token is the server's `OYI_DEVICE_TOKEN` (the app sends it as
`Authorization: Bearer <token>`).

## Server configuration

```bash
# .env on the OYI host
NEBIUS_API_KEY=<nebius token factory key>
OYI_DEVICE_TOKEN=<shared secret for the phone>
OYI_DEVICE_API_TOKEN=<shared secret for /api/device/* callers>
OYI_DEVICE_VISION_MODEL=nvidia/Nemotron-3.5-Super-VL
```

The bot (or any other channel) also needs to know where the gateway lives:

```bash
OYI_DEVICE_GATEWAY_URL=https://<webapp-host>
```

## Drive the phone from chat

```
/device open Settings and turn on dark mode
/devices
```

or let the agent do it — the `control_phone` tool is registered with the OYI
agent, so a plain message like "open WhatsApp on my phone" works from
Telegram, the voice webapp, or the live chat.

## Safety notes

- The vision prompt forbids entering passwords, PINs, payment data and 2FA
  codes, and forbids destructive actions that were not explicitly requested.
- Screen content is treated as untrusted data (prompt-injection guard).
- Secure screens (banking, keyguard, FLAG_SECURE) do not expose their content,
  so the planner will ask the user instead of guessing.
- The token is stored in an `EncryptedSharedPreferences` file.
- v1 is a sideloaded personal app: it uses `PACKAGE_USAGE_STATS` and an
  accessibility service, which are not Play-store friendly.
