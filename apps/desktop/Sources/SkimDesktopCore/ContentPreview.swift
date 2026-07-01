import Foundation

public enum ContentPreview: Equatable, Sendable {
    case youtube(originalURL: URL, embedURL: URL, videoID: String)
    case external(URL)

    public static func classify(_ url: URL) -> ContentPreview {
        if let youtube = youtubePreview(url) {
            return youtube
        }
        return .external(url)
    }

    private static func youtubePreview(_ url: URL) -> ContentPreview? {
        guard let host = url.host()?.lowercased() else {
            return nil
        }

        let videoID: String?
        if host == "youtu.be" {
            videoID = url.pathComponents.dropFirst().first
        } else if ["youtube.com", "www.youtube.com", "m.youtube.com"].contains(host) {
            videoID = youtubeVideoID(from: url)
        } else {
            videoID = nil
        }

        guard let videoID, !videoID.isEmpty,
              let embedURL = URL(string: "https://www.youtube.com/embed/\(videoID)")
        else {
            return nil
        }

        return .youtube(originalURL: url, embedURL: embedURL, videoID: videoID)
    }

    private static func youtubeVideoID(from url: URL) -> String? {
        let parts = url.pathComponents.filter { $0 != "/" }
        if parts.count >= 2, parts[0] == "shorts" || parts[0] == "embed" {
            return parts[1]
        }

        guard parts.first?.lowercased() == "watch",
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        else {
            return nil
        }
        return components.queryItems?.first { $0.name == "v" }?.value
    }
}
