import SkimDesktopCore
import SwiftUI
import WebKit

struct PreviewPane: View {
    let preview: ContentPreview

    var body: some View {
        switch preview {
        case let .youtube(originalURL, embedURL, videoID):
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("YouTube 미리보기")
                        .font(.headline)
                    Spacer()
                    Link("외부에서 열기", destination: originalURL)
                }
                YouTubeWebPreview(embedURL: embedURL, videoID: videoID)
                    .frame(minHeight: 260)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        case let .external(url):
            VStack(alignment: .leading, spacing: 10) {
                Text("미리보기 불가")
                    .font(.headline)
                Text(url.absoluteString)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .textSelection(.enabled)
                Link("외부에서 열기", destination: url)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8))
        }
    }
}

private struct YouTubeWebPreview: NSViewRepresentable {
    let embedURL: URL
    let videoID: String

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context _: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.allowsAirPlayForMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.allowsBackForwardNavigationGestures = false
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        let currentIframeURL = iframeURL
        guard context.coordinator.loadedIframeURL != currentIframeURL else {
            return
        }
        context.coordinator.loadedIframeURL = currentIframeURL
        webView.loadHTMLString(html, baseURL: URL(string: embedPageOrigin))
    }

    final class Coordinator {
        var loadedIframeURL: String?
    }

    private var embedPageOrigin: String {
        "https://skim.local/"
    }

    private var iframeURL: String {
        guard var components = URLComponents(url: embedURL, resolvingAgainstBaseURL: false) else {
            return embedURL.absoluteString
        }
        components.queryItems = [
            URLQueryItem(name: "enablejsapi", value: "1"),
            URLQueryItem(name: "playsinline", value: "1"),
            URLQueryItem(name: "origin", value: "https://skim.local")
        ]
        return components.url?.absoluteString ?? embedURL.absoluteString
    }

    private var html: String {
        """
        <!doctype html>
        <html>
          <head>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
              html, body { margin: 0; height: 100%; background: #0f1115; overflow: hidden; }
              iframe { border: 0; width: 100%; height: 100%; }
            </style>
          </head>
          <body>
            <iframe
              src="\(iframeURL)"
              title="YouTube video \(videoID)"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
              allowfullscreen>
            </iframe>
          </body>
        </html>
        """
    }
}
