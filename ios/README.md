# Spread — iPhone app

A tiny native iOS app (SwiftUI) that displays the existing web dashboard
(`dashboard.py`) full-screen, so you can watch the spreads on your phone like a
normal app. It's a thin `WKWebView` wrapper: it doesn't reimplement the
dashboard, it just loads it — so every change you make to the web dashboard
shows up here automatically (including dark mode).

Nothing in the Python project changes. This lives entirely in `ios/`.

## What you need

- A Mac with **Xcode** installed.
- An **Apple ID** (a free one is fine — no $99 Developer Program required).
- Your iPhone and Mac on the **same Wi-Fi** network.

> Free Apple ID caveat: apps installed this way are signed for **7 days**. After
> that the app stops launching until you plug the phone back into the Mac and
> press Run again in Xcode. (Paying for the Developer Program extends this to a
> year, and tools like AltStore/SideStore can auto-refresh the 7-day signature.)

## 1. Serve the dashboard to your network

By default `dashboard.py` only listens on localhost. Start it bound to all
interfaces so your phone can reach it:

```bash
# from the project root (one level up from ios/)
DASHBOARD_HOST=0.0.0.0 python3 dashboard.py
```

Find your Mac's LAN IP address:

```bash
ipconfig getifaddr en0   # Wi-Fi (try en1 if this is blank)
```

Your dashboard is now reachable at `http://<that-IP>:8000/app`.

## 2. Open and run the app

1. Open `ios/Spread.xcodeproj` in Xcode.
2. Select the **Spread** scheme and your iPhone as the run destination (plug it
   in the first time; enable Developer Mode on the phone if prompted).
3. Set signing: target **Spread ▸ Signing & Capabilities ▸ Team** → pick your
   personal Apple ID. If the bundle id `com.arb.spread` is taken, change it to
   something unique like `com.<yourname>.spread`.
4. Press **Run** (⌘R). The app installs on your phone.
5. On first launch, tap the **gear** button (bottom-right) and enter
   `http://<your-mac-IP>:8000/app`, then **Save**. This is remembered.

The first time it connects over the local network, iOS shows a "find devices on
your local network" prompt — allow it.

## Using it

- **Pull down** to refresh, or tap the **↻** button.
- Tap the **gear** to change the server address (e.g. if your Mac's IP changes).
- Dark mode follows the dashboard's own toggle / your system appearance.

## Notes & options

- **Away from home?** This setup only works on the same Wi-Fi. To use it
  anywhere, run the dashboard behind **Tailscale** (put your Mac's Tailscale IP
  in settings) or host it, then point the app there.
- **Non-HTTPS local server:** the app's `Info.plist` includes an App Transport
  Security exception so it can load `http://` LAN addresses. If you serve over
  HTTPS later, you can tighten that.
- **App icon:** the icon slot is intentionally empty (you'll just get a blank/
  default icon). Drop a 1024×1024 PNG into
  `Spread/Assets.xcassets/AppIcon.appiconset` if you want a real one.

## Files

```
ios/
├─ Spread.xcodeproj/          # Xcode project
└─ Spread/
   ├─ SpreadApp.swift         # @main app entry
   ├─ ContentView.swift       # full-screen web view + controls
   ├─ WebView.swift           # WKWebView wrapper (pull-to-refresh, loading)
   ├─ SettingsView.swift      # edit the dashboard URL on-device
   ├─ Info.plist              # ATS + local-network permission
   └─ Assets.xcassets         # app icon + accent color
```
