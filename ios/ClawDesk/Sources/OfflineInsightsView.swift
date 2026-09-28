import SwiftUI
import NaturalLanguage

/// Native, genuinely on-device language classification / named entity detection.
/// This works without a provider key or internet on supported iOS devices. It
/// does not impersonate a generative local large-language model.
struct OfflineInsightsView: View {
    @State private var sourceText = ""
    @State private var analysis = ""
    @State private var lastRun: Date?

    var body: some View {
        Form {
            Section {
                Label("On-device only", systemImage: "lock.iphone")
                    .font(.headline)
                    .foregroundStyle(.green)
                Text("Paste text and analyze it using Apple's built-in Natural Language framework. No server, model download or account is required.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                TextEditor(text: $sourceText)
                    .frame(minHeight: 190)
                    .accessibilityLabel("Text for offline analysis")
                HStack {
                    Text("\(sourceText.count)/12000")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Analyze offline") {
                        runAnalysis()
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(sourceText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                              || sourceText.count > 12000)
                }
            } header: {
                Text("Text analysis")
            }

            if !analysis.isEmpty {
                Section("Result") {
                    Text(analysis)
                        .textSelection(.enabled)
                        .font(.body)
                    if let lastRun {
                        Text("Processed locally: \(lastRun.formatted())")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            Section {
                Text("Offline language detection and named-entity recognition are native machine-learning capabilities. Full offline generative chat is a separate integration requiring a compatible model, tokenizer and device benchmarking.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func runAnalysis() {
        let text = String(sourceText.prefix(12000))
        let recognizer = NLLanguageRecognizer()
        recognizer.processString(text)
        let language = recognizer.dominantLanguage?.rawValue ?? "unknown"
        let tagger = NLTagger(tagSchemes: [.nameType])
        tagger.string = text
        var entities: [String] = []
        let range = text.startIndex..<text.endIndex
        tagger.enumerateTags(in: range, unit: .word, scheme: .nameType,
                             options: [.omitPunctuation, .omitWhitespace, .joinNames]) {
            tag, tokenRange in
            if let tag, tag == .personalName || tag == .placeName || tag == .organizationName {
                let name = String(text[tokenRange])
                if !entities.contains(name) { entities.append(name) }
            }
            return true
        }
        let tokenizer = NLTokenizer(unit: .sentence)
        tokenizer.string = text
        var sentences = 0
        tokenizer.enumerateTokens(in: range) { _, _ in
            sentences += 1
            return true
        }
        let matched = entities.isEmpty ? "No named entities recognized." : entities.prefix(30).joined(separator: ", ")
        analysis = """
        Detected language: \(language)
        Sentence count: \(sentences)
        Characters: \(text.count)

        Named entities (best-effort):
        \(matched)

        This analysis ran on your iPhone. No text was uploaded.
        """
        lastRun = Date()
    }
}
