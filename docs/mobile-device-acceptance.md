# Phone and tablet acceptance

Status: physical-device results pending. The user will run these checks. Record physical results separately from browser emulation and mock-robot automation. The local implementation report is `audit/MOBILE-IMPLEMENTATION.md`.

## Start a disposable mock dashboard

The launcher requires frontend dependencies and `server/.venv` with both `server` and `mock-robot` installed (the existing development setup). From the repository root, build the frontend, then start the test server. This uses a temporary database and a simulated robot. It does not deploy to, or connect to, PatrolBot hardware. The server uses port 8127 and stays running until Ctrl+C.

```sh
npm --prefix frontend run build
python frontend/integration/serve_mock.py --host 0.0.0.0
```

Open `http://<computer-LAN-IP>:8127` on a device on the same network. The default isolated test account is an administrator. Ctrl+C stops the server and mock and removes the temporary database. Existing server data and dashboard layouts are unaffected.

## Record devices and versions

Use Safari on iPhone/iPad and stable Chrome on Android. For Apple devices, cover the current and immediately preceding major iOS/iPadOS releases; record the exact installed versions instead of assuming them from a device name.

| Device | Model | OS version | Browser version | Portrait | Landscape | Result / issue |
| --- | --- | --- | --- | --- | --- | --- |
| iPhone | | | | | | Pending |
| iPad | | | | | | Pending |
| Android phone | | | | | | Pending |
| Android tablet | | | | | | Pending |

## Check each device in both orientations

Mark each item pass/fail; attach the device name and a short reproduction for failures.

1. **Shell and reading.** Robot name, status, connection, Stop, and Menu stay reachable. Check Operator, Research, and Diagnostics presets in both themes. Scroll through every widget, including long alerts/recording names. No page-level horizontal scrolling, clipped action buttons, or unreadable values. Browser zoom still works outside an interacting map.
2. **Keyboard and dialogs.** Edit a recording name and use Save current as… with the software keyboard open. Fields stay readable without Safari auto-zoom; Save/Cancel/close controls remain reachable. Close the widget library, map fullscreen, and motor confirmation. With a keyboard attached, Tab stays inside modal dialogs, Escape closes the top dialog, and focus returns to the triggering control.
3. **Customization.** Menu → Edit dashboard → Add widget. Add/remove a widget, drag its dedicated handle, and use Move up/down and Taller/Shorter in its menu. Tablet widgets also support width changes. Minimize, rotate, and expand: expanded dimensions survive. Reload after an edit: it persists. Resize the desktop browser too: phone/tablet editing must not rearrange its desktop layout. Widget selection is shared. Save, switch to, and delete a named dashboard.
4. **Embedded maps.** Swipe on the map before choosing Interact with map: the dashboard scrolls, and no command is sent. Choose Interact with map: one finger pans, two fingers zoom; zoom buttons, Fit, Follow, and Layers work. Done restores page scrolling. Repeat on replay. Check responsiveness with the largest available map and a long recording.
5. **Touch command preview — mock only.** Set Robot Location opens/scrolls to the live map; phones open fullscreen. A tap shows a position preview but does not send. Adjust heading, then Cancel. Repeat and confirm Set location exactly once. Send Robot Here similarly requires Send destination. A simple tap retains default heading. Adding a second finger while selecting changes to pan/zoom without submitting. Repeat with the map removed from the dashboard: the temporary map must not add a saved widget.
6. **Cancellation and lifecycle — mock only.** Start a selection, then switch apps, lock/unlock, or disconnect/reconnect the device network. No stale preview may be confirmed on return. Closing fullscreen/Done cancels selection. On an iPad/tablet with a mouse, release-to-send remains the mouse behavior; touch still requires confirmation. Stop remains available in fullscreen, and command progress/outcomes remain visible. Check Resume and Cancel after Stop.
7. **Status, charging, and alerts.** Battery/computer chart taps retain a readable historical value. Read health, network, and bumper displays. Mark alerts read, filter severity, and expand Seen history. Hardware actions keep their visible rejection reasons and confirmations; use only an isolated mock/test fixture for motor/undock checks. Existing unit/browser checks cover these gates; this test launcher’s calm mock may not naturally enter every charging state.
8. **Recording and replay.** Choose channels, start/stop a named recording, replay it in a new tab, play/pause, scrub, change speed, and seek an alert when present. Rotate during replay. Export selected channels; confirm a ZIP downloads and opens in the device’s Files/Downloads app. Return to the dashboard and delete the test recording as administrator.
9. **Account and help.** Run the guided tour with each preset and restart it from Menu. Check it points at visible controls. OIDC sign-in/sign-out and observer/operator restrictions require a separate authorized OIDC test instance; the disposable local server supplies an administrator account only. Never record those checks as passed using the local administrator instance.

## Report back

```text
Device / OS / browser:
Portrait + landscape:
Failed checklist item(s) and reproduction:
Keyboard / gestures / downloads / background-resume:
OIDC account roles tested (or pending):
Map dimensions and recording duration/sample count:
Overall: PASS / FAIL / INCOMPLETE
```

Physical robot acceptance and production deployment remain separate from this checklist.
