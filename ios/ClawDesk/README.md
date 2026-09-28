# Claw Desk native iOS target (source project)

This is a genuine **SwiftUI source project** in addition to the Safari-installable PWA. It has a private hosted Claw tab and an independently usable **on-device NaturalLanguage tab** for offline language classification and named-entity recognition. That offline analysis runs without any provider key, network access, model download or host. It is NOT offline generative chat. A full offline LLM would need a licensed small model, tokenizer, quantization, runtime integration and physical-device benchmarking.

## Build from a Mac (requires Xcode and XcodeGen)

1. Install Xcode and XcodeGen on your Mac (e.g. brew install xcodegen).
2. From this folder run:

   xcodegen generate
   xcodebuild -project ClawDesk.xcodeproj -scheme ClawDesk -sdk iphonesimulator -configuration Debug CODE_SIGNING_ALLOWED=NO build

3. Open ClawDesk.xcodeproj in Xcode, select your physical iPhone, pick your Apple development team and build/install.
4. Run the existing private host, configure Tailscale on the iPhone, and enter the exact HTTPS https://your-device.tailnet.ts.net root URL. The owner key is entered inside Claw Desk; it is not embedded in the native app.
5. Switch to the Offline tab to analyze pasted text without a network or host.

**App Store submission is not automatic.** Replace the example bundle identifier, supply artwork and a privacy disclosure, enroll in Apple's Developer Program, sign an archive, run device testing and submit through App Store Connect. Approval is Apple-controlled and not guaranteed.

Google OAuth setup should ideally be completed from your trusted host's localhost browser first, since Google login in embedded WKWebView is disallowed. The native app opens off-host HTTPS links (including Google's consent page) in Safari.

Do not put Anthropic/OpenAI/Google client secrets in Swift files, Info.plist or the Git repository. Keys stay on the private host. 
