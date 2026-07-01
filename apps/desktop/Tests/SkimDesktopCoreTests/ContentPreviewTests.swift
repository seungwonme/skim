import Foundation
import Testing

@testable import SkimDesktopCore

@Test(arguments: [
    "https://www.youtube.com/watch?v=abc123XYZ09",
    "https://m.youtube.com/watch?v=abc123XYZ09&t=12",
    "https://www.youtube.com/shorts/abc123XYZ09",
    "https://www.youtube.com/embed/abc123XYZ09",
    "https://youtu.be/abc123XYZ09"
])
func classifiesYouTubeURLsAsEmbed(input: String) throws {
    let preview = ContentPreview.classify(try #require(URL(string: input)))

    guard case let .youtube(_, embedURL, videoID) = preview else {
        Issue.record("Expected YouTube preview for \(input)")
        return
    }

    #expect(videoID == "abc123XYZ09")
    #expect(embedURL.absoluteString == "https://www.youtube.com/embed/abc123XYZ09")
}

@Test
func classifiesUnsupportedURLsAsExternalFallback() throws {
    let url = try #require(URL(string: "https://example.com/article"))
    let preview = ContentPreview.classify(url)

    #expect(preview == .external(url))
}
