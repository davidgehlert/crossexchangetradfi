import SwiftUI

/// Full-screen wrapper around the existing web dashboard. The server URL is
/// stored in UserDefaults (via @AppStorage) so it can be changed on-device
/// without recompiling — handy when your Mac's LAN IP changes.
struct ContentView: View {
    // Point this at the machine running `python3 dashboard.py`. Start the
    // dashboard with DASHBOARD_HOST=0.0.0.0 so it accepts LAN connections,
    // then set this to http://<your-mac-LAN-IP>:8000/app in the app's settings.
    @AppStorage("serverURL") private var serverURL: String = "http://192.168.1.42:8000/app"

    @State private var showSettings = false
    @State private var reloadToken = 0
    @State private var isLoading = false
    @State private var lastError: String?

    private var trimmedURL: String {
        serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            Color(.systemBackground).ignoresSafeArea()

            if let url = URL(string: trimmedURL), url.scheme != nil {
                WebView(
                    url: url,
                    reloadToken: reloadToken,
                    isLoading: $isLoading,
                    lastError: $lastError
                )
                .ignoresSafeArea(.container, edges: .bottom)
            } else {
                invalidURLView
            }

            controls
                .padding(16)
        }
        .overlay(alignment: .top) { loadingBar }
        .sheet(isPresented: $showSettings) {
            SettingsView(serverURL: $serverURL) { reloadToken += 1 }
        }
    }

    @ViewBuilder
    private var loadingBar: some View {
        if isLoading {
            ProgressView()
                .progressViewStyle(.circular)
                .padding(8)
                .background(.ultraThinMaterial, in: Capsule())
                .padding(.top, 4)
        }
    }

    private var controls: some View {
        HStack(spacing: 10) {
            circleButton(system: "arrow.clockwise") { reloadToken += 1 }
            circleButton(system: "gearshape.fill") { showSettings = true }
        }
    }

    private func circleButton(system: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: system)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 42, height: 42)
                .background(.black.opacity(0.55), in: Circle())
                .overlay(Circle().strokeBorder(.white.opacity(0.15)))
        }
    }

    private var invalidURLView: some View {
        VStack(spacing: 12) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 40))
                .foregroundStyle(.secondary)
            Text("Set your dashboard address")
                .font(.headline)
            Text(lastError ?? "Tap the gear to enter the URL your computer serves the dashboard on.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("Open settings") { showSettings = true }
                .buttonStyle(.borderedProminent)
        }
        .padding(32)
    }
}

#Preview {
    ContentView()
}
