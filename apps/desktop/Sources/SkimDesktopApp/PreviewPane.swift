import AppKit
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
                    Button("외부에서 열기") { NSWorkspace.shared.open(originalURL) }
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
                Button("외부에서 열기") { NSWorkspace.shared.open(url) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8))
        }
    }
}

/// 바깥 SwiftUI `ScrollView` 안에 높이를 고정해 넣는 웹뷰. 세로로는 스크롤할 것이 없으므로
/// 세로 휠 이벤트를 바깥 스크롤 뷰로 넘긴다. `WKWebView`는 휠을 직접 받아 삼켜서, 커서가
/// 본문 위에 있으면 리더 칸이 스크롤되지 않았다 (#78). 가로 휠은 웹뷰에 남긴다. 본문의
/// 긴 코드 블록(`pre`, `overflow-x: auto`)이 가로로 스크롤해야 한다.
final class PassthroughScrollWebView: WKWebView {
    /// 트랙패드 제스처는 시작할 때 방향을 정해 끝(관성 포함)까지 같은 쪽으로 보낸다.
    /// 이벤트마다 정하면 변화량 0인 끝 이벤트가 다른 쪽으로 가 스크롤이 끊긴다.
    private var routesToParent: Bool?

    override func scrollWheel(with event: NSEvent) {
        let startsGesture = event.phase == .mayBegin || event.phase == .began
        let isWheelClick = event.phase.isEmpty && event.momentumPhase.isEmpty
        if startsGesture || isWheelClick {
            routesToParent = nil
        }
        if routesToParent == nil, event.scrollingDeltaX != 0 || event.scrollingDeltaY != 0 {
            routesToParent = abs(event.scrollingDeltaY) >= abs(event.scrollingDeltaX)
        }
        if routesToParent ?? true {
            nextResponder?.scrollWheel(with: event)
        } else {
            super.scrollWheel(with: event)
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
        let webView = PassthroughScrollWebView(frame: .zero, configuration: configuration)
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
