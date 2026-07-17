import SwiftUI

/// Lets you set the dashboard URL on-device (no recompile needed).
struct SettingsView: View {
    @Binding var serverURL: String
    var onSave: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var draft: String = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Dashboard address") {
                    TextField("http://192.168.1.42:8000/app", text: $draft)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled(true)
                        .keyboardType(.URL)
                        .font(.system(.body, design: .monospaced))
                }

                Section("How to find it") {
                    Text("""
                    1. On your computer, run the dashboard so it accepts LAN \
                    connections:
                       DASHBOARD_HOST=0.0.0.0 python3 dashboard.py
                    2. Find your computer's local IP (System Settings ▸ Wi-Fi ▸ \
                    Details, or `ipconfig getifaddr en0`).
                    3. Enter http://THAT-IP:8000/app above.

                    Your phone must be on the same Wi-Fi as the computer.
                    """)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        serverURL = draft.trimmingCharacters(in: .whitespacesAndNewlines)
                        onSave()
                        dismiss()
                    }
                    .fontWeight(.semibold)
                }
            }
            .onAppear { draft = serverURL }
        }
    }
}
