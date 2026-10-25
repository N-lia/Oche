#!/usr/bin/env bash
# End-to-end proof for the OYI Android app.
#
# Runs the whole product loop on a real Android 14 emulator:
#
#   app -> OYI gateway -> nvidia/Nemotron-3.5-Super-VL -> app -> real UI
#
# It checks that the app installs, starts, shows the overlay bubble, links to
# the gateway over the outbound WebSocket, reports its usage, and that a plain
# language command actually opens an app on the device.
#
# Requirements (one-time setup):
#   sdkmanager "emulator" "platform-tools" "platforms;android-35" \
#              "build-tools;35.0.0" "system-images;android-34;google_apis;x86_64"
#   avdmanager create avd -n oyi34 -k "system-images;android-34;google_apis;x86_64" \
#              -d pixel_5 --force
#
# Usage:
#   ANDROID_SDK_ROOT=... bash tools/emulator_test.sh android/app/build/outputs/apk/debug/app-debug.apk
#
# Environment:
#   ANDROID_SDK_ROOT  Android SDK location (default /tmp/opencode/toolchain/sdk)
#   AVD_NAME          AVD to use (default oyi34)
#   KEEP_EMU=1        leave the emulator running after the test
set -uo pipefail

APK="${1:?usage: emulator_test.sh <path-to-app-debug.apk>}"
SDK="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-/tmp/opencode/toolchain/sdk}}"
ADB="$SDK/platform-tools/adb"
EMU="$SDK/emulator/emulator"
AVD="${AVD_NAME:-oyi34}"
PKG=ai.oyi.device.debug
SERVICE=ai.oyi.device.accessibility.OyiAccessibilityService
TOKEN="${OYI_TEST_TOKEN:-local-test-token}"
PORT="${OYI_TEST_PORT:-8099}"
GATEWAY_URL="http://127.0.0.1:$PORT"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UI=/tmp/opencode/ui.xml
PASS=0
FAIL=0

say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '  PASS %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  FAIL %s\n' "$*"; FAIL=$((FAIL+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

ui_dump() {
  rm -f "$UI"
  for _ in 1 2 3 4; do
    $ADB shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1
    $ADB pull /sdcard/ui.xml "$UI" >/dev/null 2>&1
    if [ -s "$UI" ]; then return 0; fi
    sleep 1
  done
  echo "  (ui dump failed)" >&2
  return 1
}

find_text() {   # find_text <substring> -> "x y" of the node centre
  ui_dump
  python3 - "$1" <<'PY'
import re, sys
needle = sys.argv[1].lower()
xml = open('/tmp/opencode/ui.xml', encoding='utf-8', errors='ignore').read()
for node in re.findall(r'<node[^>]*>', xml):
    attrs = dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', node))
    hay = ' '.join([attrs.get('text', ''), attrs.get('content-desc', ''), attrs.get('hint', '')]).lower()
    if needle in hay:
        m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', attrs.get('bounds', ''))
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            print((x1 + x2) // 2, (y1 + y2) // 2)
            break
PY
}

find_switch() {  # find_switch <index> -> "x y" of the Nth checkable node
  ui_dump
  python3 - "$1" <<'PY'
import re, sys
idx = int(sys.argv[1])
xml = open('/tmp/opencode/ui.xml', encoding='utf-8', errors='ignore').read()
hits = []
for node in re.findall(r'<node[^>]*>', xml):
    attrs = dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', node))
    if attrs.get('checkable') == 'true' and attrs.get('clickable') == 'true':
        m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', attrs.get('bounds', ''))
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            hits.append(((x1 + x2) // 2, (y1 + y2) // 2))
if len(hits) > idx:
    print(hits[idx][0], hits[idx][1])
PY
}

tap_text() {
  local coords
  coords=$(find_text "$1")
  if [ -z "$coords" ]; then echo "  (could not find '$1')"; return 1; fi
  # shellcheck disable=SC2086
  $ADB shell input tap $coords
  sleep 0.8
  return 0
}

tap_switch() {
  local coords
  coords=$(find_switch "${1:-0}")
  if [ -z "$coords" ]; then echo "  (no switch #$1 found)"; return 1; fi
  # shellcheck disable=SC2086
  $ADB shell input tap $coords
  sleep 1.2
  return 0
}

say "0. Start the OYI gateway"
cd "$REPO" || exit 1
export OYI_DEVICE_TOKEN=$TOKEN
export OYI_DEVICE_API_TOKEN=$TOKEN
export WEBAPP_PORT=$PORT
if [ -z "${NEBIUS_API_KEY:-}" ] && [ -f "$REPO/.env" ]; then
  export NEBIUS_API_KEY="$(grep -E '^NEBIUS_API_KEY' "$REPO/.env" | cut -d= -f2- | tr -d ' ')"
fi
"$REPO/.venv/bin/python" webapp_server.py > /tmp/opencode/gateway.log 2>&1 &
GW_PID=$!
for _ in $(seq 1 60); do
  if curl -s -o /dev/null -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/api/device/health"; then
    break
  fi
  sleep 1
done
check "gateway is up" "curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer $TOKEN' http://127.0.0.1:$PORT/api/device/health | grep -q 200"

say "1. Boot the Android 14 emulator"
system_ready() {
  # sys.boot_completed flips before every system service is up, so wait for
  # the ones this test actually needs.
  [ "$($ADB shell getprop init.svc.bootanim 2>/dev/null | tr -d '\r')" = "stopped" ] &&
  $ADB shell service check activity 2>/dev/null | grep -q "found" &&
  $ADB shell service check package 2>/dev/null | grep -q "found"
}

if $ADB devices | grep -q "emulator-"; then
  echo "  (reusing the running emulator)"
else
  $EMU -avd "$AVD" -no-window -no-audio -no-boot-anim -gpu swiftshader_indirect \
       -no-snapshot -accel on -memory 3072 > /tmp/opencode/emu.log 2>&1 &
  $ADB wait-for-device >/dev/null 2>&1
fi
for _ in $(seq 1 150); do
  if system_ready; then break; fi
  sleep 4
done
check "system services are ready" "system_ready"
check "device runs Android 14 (API 34)" "$ADB shell getprop ro.build.version.sdk | grep -q 34"
sleep 5

$ADB shell settings put global hide_error_dialogs 1
$ADB shell settings put global window_animation_scale 0
$ADB shell settings put global transition_animation_scale 0
$ADB shell settings put global animator_duration_scale 0

say "2. Install the APK"
$ADB uninstall $PKG >/dev/null 2>&1
for attempt in 1 2 3; do
  if $ADB install -r "$APK" 2>&1 | tail -2 | grep -q "Success"; then break; fi
  echo "  (install attempt $attempt failed, waiting for the package service)"
  for _ in $(seq 1 20); do
    $ADB shell service check package 2>/dev/null | grep -q "found" && break
    sleep 3
  done
  sleep 5
done
check "package installed" "$ADB shell pm list packages | grep -q $PKG"

say "3. Grant permissions"
$ADB shell appops set $PKG SYSTEM_ALERT_WINDOW allow
$ADB shell appops set $PKG GET_USAGE_STATS allow
$ADB shell pm grant $PKG android.permission.POST_NOTIFICATIONS >/dev/null 2>&1
$ADB shell settings put secure enabled_accessibility_services "$PKG/$SERVICE"
$ADB shell settings put secure accessibility_enabled 1
sleep 3
check "accessibility service enabled" "$ADB shell settings get secure enabled_accessibility_services | grep -q OyiAccessibilityService"
check "overlay permission granted" "$ADB shell appops get $PKG SYSTEM_ALERT_WINDOW | grep -q allow"

say "4. Launch the app"
$ADB logcat -c
# The package manager can take a moment to register a freshly installed app.
for _ in $(seq 1 6); do
  if $ADB shell am start -W -n "$PKG/ai.oyi.device.MainActivity" 2>&1 | grep -q "Error type 3"; then
    sleep 5
    continue
  fi
  break
done
$ADB shell am start -W -n "$PKG/ai.oyi.device.MainActivity" 2>&1 | tail -3
sleep 5
app_running() { $ADB shell pidof $PKG 2>/dev/null | grep -q .; }
for _ in $(seq 1 10); do
  app_running && break
  sleep 2
done
check "app process is running" "app_running"
check "no fatal exception at startup" "! $ADB logcat -d -s AndroidRuntime:E | grep -q 'FATAL EXCEPTION'"

# The emulator's default route can point at a dead interface, so the device
# reaches the host through an adb reverse tunnel on its own loopback.
$ADB reverse tcp:$PORT tcp:$PORT >/dev/null 2>&1
check "adb reverse tunnel is up" "$ADB reverse --list | grep -q 'tcp:$PORT'"

ensure_app_ui() {
  for _ in 1 2 3 4 5 6; do
    $ADB shell am start -n "$PKG/ai.oyi.device.MainActivity" >/dev/null 2>&1
    sleep 2
    ui_dump || true
    if grep -q "Link to OYI" "$UI" 2>/dev/null; then return 0; fi
  done
  return 1
}

say "5. Point the app at the local gateway"
ensure_app_ui || bad "could not bring the OYI screen to the front"
$ADB shell am start -n "$PKG/ai.oyi.device.MainActivity" \
  --es oyi_gateway_url "$GATEWAY_URL" --es oyi_token "$TOKEN" >/dev/null 2>&1
sleep 3
ui_dump || true
check "URL field holds the gateway" "grep -q '127.0.0.1:$PORT' \$UI"
check "no fatal exception after saving" "! $ADB logcat -d -s AndroidRuntime:E | grep -q 'FATAL EXCEPTION'"

say "6. Turn on the OYI overlay (real UI switch)"
$ADB shell input keyevent 111
sleep 1
$ADB shell input swipe 500 1900 500 900 300
sleep 1.5
if ! tap_switch 0; then
  bad "could not find the overlay switch"
  grep -o 'checkable="true"[^>]*' "$UI" | head -5
fi
sleep 4
check "service is running" "$ADB shell dumpsys activity services $PKG | grep -q OyiService"
check "overlay window is on screen" "$ADB shell dumpsys window windows | grep -qE 'Window\{[^}]* u0 ai.oyi.device.debug'"
check "no fatal exception after service start" "! $ADB logcat -d -s AndroidRuntime:E | grep -q 'FATAL EXCEPTION'"

say "7. The phone links to the gateway"
$ADB shell am start -n "$PKG/ai.oyi.device.MainActivity" >/dev/null 2>&1
sleep 3
ui_dump || true
echo "     link badge: $(grep -oE 'text="(Connected|Connecting|Offline|Error)"' "$UI" | head -1)"
for _ in $(seq 1 30); do
  if grep -q "Device connected" /tmp/opencode/gateway.log 2>/dev/null; then break; fi
  sleep 1
done
check "gateway logged the phone" "grep -q 'Device connected' /tmp/opencode/gateway.log"
check "phone listed by /api/device/devices" "curl -s -H 'Authorization: Bearer $TOKEN' http://127.0.0.1:$PORT/api/device/devices | grep -q device_id"

say "8. Phone status over the same socket"
STATUS=$(curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/api/device/status")
echo "     $STATUS"
check "phone answered the status request" "echo '$STATUS' | grep -q 'app_label'"

say "9. Real vision control: open the Settings app"
BEFORE=$($ADB shell dumpsys activity activities | grep -m1 "topResumedActivity")
echo "     before: $BEFORE"
RESULT=$(curl -s --max-time 240 -X POST "http://127.0.0.1:$PORT/api/device/command" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"text":"open the Settings app on the phone","max_steps":6,"timeout_s":200}')
echo "     result: $RESULT"
check "command completed" "echo '$RESULT' | grep -q '\"status\":\"completed\"'"
AFTER=$($ADB shell dumpsys activity activities | grep -m1 "topResumedActivity")
echo "     after:  $AFTER"
check "Settings is now in the foreground" "echo '$AFTER' | grep -qi 'com.android.settings'"

say "10. Usage tracking wrote to the local database"
check "usage database exists" "$ADB shell run-as $PKG ls databases/ | grep -q oyi_usage"

say "11. Clean run"
check "no fatal exception overall" "! $ADB logcat -d -s AndroidRuntime:E | grep -q 'FATAL EXCEPTION'"
$ADB logcat -d -s OyiLink:I | tail -5

printf '\n== RESULT: %d passed, %d failed ==\n' "$PASS" "$FAIL"
if [ "${KEEP_EMU:-0}" != "1" ]; then $ADB emu kill >/dev/null 2>&1; fi
kill $GW_PID 2>/dev/null
[ "$FAIL" -eq 0 ]
