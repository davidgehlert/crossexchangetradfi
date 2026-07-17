import SwiftUI
import WebKit

/// A thin SwiftUI wrapper over WKWebView with pull-to-refresh, loading state,
/// and error reporting. It reloads whenever `reloadToken` changes.
struct WebView: UIViewRepresentable {
    let url: URL
    var reloadToken: Int
    @Binding var isLoading: Bool
    @Binding var lastError: String?

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true
        webView.scrollView.contentInsetAdjustmentBehavior = .always
        webView.isOpaque = false

        let refresh = UIRefreshControl()
        refresh.addTarget(
            context.coordinator,
            action: #selector(Coordinator.handleRefresh(_:)),
            for: .valueChanged
        )
        webView.scrollView.refreshControl = refresh

        context.coordinator.webView = webView
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        // Keep bindings fresh for delegate callbacks after SwiftUI re-renders.
        context.coordinator.parent = self

        if context.coordinator.lastReloadToken != reloadToken {
            context.coordinator.lastReloadToken = reloadToken
            webView.load(URLRequest(url: url))
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var parent: WebView
        weak var webView: WKWebView?
        var lastReloadToken: Int

        init(_ parent: WebView) {
            self.parent = parent
            self.lastReloadToken = parent.reloadToken
        }

        @objc func handleRefresh(_ sender: UIRefreshControl) {
            webView?.reload()
        }

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            parent.isLoading = true
            parent.lastError = nil
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            parent.isLoading = false
            webView.scrollView.refreshControl?.endRefreshing()
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            finish(with: error, on: webView)
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            finish(with: error, on: webView)
        }

        private func finish(with error: Error, on webView: WKWebView) {
            // Ignore "cancelled" (code -999) which fires on rapid reloads.
            if (error as NSError).code != NSURLErrorCancelled {
                parent.lastError = error.localizedDescription
            }
            parent.isLoading = false
            webView.scrollView.refreshControl?.endRefreshing()
        }
    }
}
