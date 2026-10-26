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

### Pairing from a terminal (debug builds)

Debug builds accept the gateway settings as intent extras, which avoids
fighting the on-screen keyboard:

```bash
adb shell am start -n ai.oyi.device.debug/ai.oyi.device.MainActivity \
  --es oyi_gateway_url http://127.0.0.1:8099 --es oyi_token "$OYI_DEVICE_TOKEN"
```

Release builds ignore these extras.

### Diagnosing the link

The app logs the connection lifecycle under the `OyiLink` tag:

```bash
adb logcat -s OyiLink:I
```

`I OyiLink : connecting to wss://host/ws/device` followed by
`I OyiLink : connected to ...` means the link is up. A failed attempt shows a
human-readable reason in the app ("Cannot reach the gateway", "Gateway host
not found", "TLS handshake failed") instead of a raw socket error.

## How the phone is controlled

The control loop follows **Google ARTEMIS** (the phone-use framework behind
AndroidWorld's SOTA results) rather than asking a model to guess pixels:

1. **Observation** — the phone publishes the visible, labelled elements of the
   screen as a numbered index, in ARTEMIS' *Visible UI Elements* format:

   ```
   [1] Text: 'Wi-Fi' | Bounds: [42,332][700,360] | id: wifi_title
   [2] Text: 'Battery' | Bounds: [42,520][700,548]
   ```

   Duplicates are collapsed, off-screen and unlabelled nodes are dropped, and
   the screenshot travels alongside it.

2. **Action** — the planner targets an element by its 1-based `target` index:

   ```json
   {"type": "tap", "target": 3}
   ```

   Raw `x`/`y` (0..1000) remain available only as a fallback for custom-drawn
   UI that exposes no accessible element — ARTEMIS' *Dynamic-First,
   Coordinate-Fallback* rule.

3. **Execution** — the *device* resolves the index against the live node it
   captured, so the tap lands on the element the model meant. If that node no
   longer accepts the click, the recorded centre is used as the fallback.

4. **Recovery** — a blocked action (the element moved, the tap missed) is
   handed back to the planner together with the reason instead of ending the
   task.

5. **Scrolling** is a slow drag that keeps a large overlap with the previous
   viewport, so no row is skipped and the screen settles where it was left.

The model never invents an index that is not in the list, and it is told when
the previous action failed so it can correct itself.

## Chat with OYI

The app has a **Chat** tab next to **Setup**:

- **Text** — `POST /api/chat/stream` on the same webapp; the reply is rendered
  token by token. Quick actions: *On my screen?*, *Time today*, *Open Settings*.
- **Live voice** — `WS /ws/voice`, the same protocol as the browser live chat:
  16 kHz mono PCM16 microphone frames up, base64 MP3 replies played locally.
  The agent's tools work here too, so you can say "open WhatsApp on my phone".

The agent runs on the server, so the phone needs the gateway configured
(**Setup → Link to OYI**) and the server needs `OYI_DEVICE_GATEWAY_URL` and
`OYI_DEVICE_API_TOKEN` for the `control_phone` tool.

## End-to-end test on an emulator

`tools/emulator_test.sh` runs the whole product loop on a real Android 14
emulator: install, overlay bubble, gateway link, usage reporting, and a
vision-guided command that actually opens an app on the device.

```bash
# one-time setup
sdkmanager "emulator" "platform-tools" "platforms;android-35" \
           "build-tools;35.0.0" "system-images;android-34;google_apis;x86_64"
avdmanager create avd -n oyi34 \
  -k "system-images;android-34;google_apis;x86_64" -d pixel_5 --force

cd android && ./gradlew assembleDebug && cd ..
ANDROID_SDK_ROOT=/path/to/sdk bash tools/emulator_test.sh \
  android/app/build/outputs/apk/debug/app-debug.apk
```

It needs `NEBIUS_API_KEY` (read from `.env` if present) and uses an
`adb reverse` tunnel so the emulator reaches the local gateway.

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

## Known Android behaviour

- **The bubble is hidden while the Settings app is open.** Android's Settings
  app sets `HIDE_NON_SYSTEM_OVERLAY_WINDOWS` on its window, which makes the
  system hide every third-party `TYPE_APPLICATION_OVERLAY` window. This is
  deliberate platform behaviour (it keeps permission dialogs readable), not
  an OYI bug — the bubble is drawn over every other app, including the
  launcher.
- The overlay uses `FLAG_NOT_FOCUSABLE | FLAG_LAYOUT_IN_SCREEN`. It must not
  use `FLAG_LAYOUT_NO_LIMITS`: that leaves the display bounds unbounded and
  the bubble's surface is then never shown.


### The planner speaks the phone-use dialect

`nvidia/Nemotron-3.5-Super-VL` is trained on phone-use data and answers in the
mobile-use/ARTEMIS vocabulary:

```json
{"action_type": "click", "action": "the text 'Wi-Fi' in the list",
 "action_ui_element": "the text 'Wi-Fi' in the list", "x": 0.42, "y": 0.51}
```

Both that and the index form (`{"type": "tap", "target": 3}`) are accepted and
normalised to one internal action. The device then resolves the element
against the live screen, so the model never has to be right about pixels.
