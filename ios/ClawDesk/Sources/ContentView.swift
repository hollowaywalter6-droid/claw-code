import SwiftUI
import WebKit

/// A native two-tab companion: private remote Claw UI + genuinely offline NLP.
/// The native app does NOT silently copy API keys or claim an offline generative LLM.
struct ContentView: View {
    @AppStorage("ClawDeskPrivateHost") private var host = ""
    @State private var hostDraft = ""
    @State private var error = ""
    @State private var showingEdit = false

    private var hostURL: URL? {
        guard let value = URL(string: host),
              let name = value.host?.lowercased(),
              value.scheme == "https",
              name.hasSuffix(".ts.net"),
              value.user == nil, value.password == nil,
              value.query == nil, value.fragment == nil,
              value.path.isEmpty || value.path == "/" else {
            return nil
        }
        return value
    }

    var body: some View {
        TabView {
            NavigationStack {
                Group {
                    if let url = hostURL {
                        RemoteClawView(url: url)
                            .ignoresSafeArea(edges: .bottom)
                    } else {
                        setupScreen
                    }
                }
                .navigationTitle("Claw Desk")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("Host") {
                            hostDraft = host
                            showingEdit = true
                        }
                    }
                }
            }
            .tabItem {
                Label("Claw", systemImage: "bubble.left.and.text.bubble.right")
            }

            NavigationStack {
                OfflineInsightsView()
                    .navigationTitle("Offline insights")
                    .navigationBarTitleDisplayMode(.inline)
            }
            .tabItem {
                Label("Offline", systemImage: "iphone.gen3")
            }
        }
        .tint(Color(red: 0.65, green: 0.95, blue: 0.76))
        .sheet(isPresented: $showingEdit) {
            NavigationStack {
                setupScreen
                    .navigationTitle("Private host")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("Cancel") { showingEdit = false }
                        }
                    }
            }
        }
    }

    private var setupScreen: some View {
        Form {
            Section {
                Text("Connect through your private Tailscale network. The Rust Claw engine runs on a trusted host, not on the iPhone.")
                    .font(.subheadline)
                TextField("https://device.tailnet.ts.net", text: $hostDraft)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .accessibilityLabel("Private Tailscale host URL")
                if !error.isEmpty {
                    Text(error).foregroundStyle(.red)
                }
                Button("Save private host") {
                    let value = hostDraft.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard let parsed = URL(string: value),
                          let name = parsed.host?.lowercased(),
                          parsed.scheme == "https", name.hasSuffix(".ts.net"),
                          parsed.user == nil, parsed.password == nil,
                          parsed.query == nil, parsed.fragment == nil,
                          parsed.path.isEmpty || parsed.path == "/" else {
                        error = "Enter the exact HTTPS root URL of your private *.ts.net host."
                        return
                    }
                    error = ""
                    host = value
                    showingEdit = false
                }
            } header: {
                Text("Your Claw Desk")
            } footer: {
                Text("Owner unlock stays in web page memory. Only the host URL is saved on the iPhone. Use Tailscale Serve, never Funnel.")
            }
            Section("On-device analysis") {
                Text("The Offline tab runs Apple's Natural Language text analysis entirely on your iPhone, even without Claw or internet access. This is not a full offline chatbot.")
            }
        }
        .onAppear {
            if hostDraft.isEmpty { hostDraft = host }
        }
    }
}

struct RemoteClawView: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        let browser = WKWebView(frame: .zero, configuration: configuration)
        browser.navigationDelegate = context.coordinator
        browser.allowsBackForwardNavigationGestures = true
        browser.isOpaque = false
        browser.backgroundColor = UIColor(red: 11/255, green: 17/255, blue: 29/255, alpha: 1)
        browser.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData))
        return browser
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {
        if context.coordinator.currentHost != (url.host ?? "") {
            context.coordinator.currentHost = url.host ?? ""
            context.coordinator.allowedHost = url.host ?? ""
            uiView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData))
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(allowedHost: url.host ?? "")
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var currentHost: String
        var allowedHost: String

        init(allowedHost: String) {
            self.allowedHost = allowedHost
            self.currentHost = allowedHost
        }

        func webView(_ webView: WKWebView,
                     decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let destination = action.request.url else {
                decisionHandler(.cancel)
                return
            }
            if destination.scheme == "https" && destination.host == allowedHost {
                decisionHandler(.allow)
                return
            }
            // Google sign-in must open in Safari; Google does not permit login in
            // embedded WKWebView. The authenticated site keeps the private host.
            decisionHandler(.cancel)
            if destination.scheme == "https" {
                UIApplication.shared.open(destination)
            }
        }
    }
}
