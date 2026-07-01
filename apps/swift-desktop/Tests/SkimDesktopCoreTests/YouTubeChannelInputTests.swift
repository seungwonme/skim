import Testing

@testable import SkimDesktopCore

@Test
func parsesYouTubeChannelURL() throws {
    let candidate = try YouTubeChannelInput.parse("https://www.youtube.com/channel/UCabc123XYZ09/videos")

    #expect(candidate.canonicalID == "UCabc123XYZ09")
    #expect(candidate.displayName == "YouTube UCabc123XYZ09")
    #expect(candidate.handleOrURL == "https://www.youtube.com/channel/UCabc123XYZ09/videos")
    #expect(candidate.draft.platform == "youtube")
    #expect(candidate.draft.sourceType == "channel")
}

@Test
func parsesYouTubeHandleURLAndBareHandle() throws {
    let fromURL = try YouTubeChannelInput.parse("https://www.youtube.com/@openai")
    let fromHandle = try YouTubeChannelInput.parse("@openai")

    #expect(fromURL.canonicalID == "@openai")
    #expect(fromHandle == fromURL)
    #expect(fromURL.handleOrURL == "https://www.youtube.com/@openai")
    #expect(fromURL.notes?.contains("channel_id resolution") == true)
}

@Test
func parsesYouTubeRSSChannelID() throws {
    let candidate = try YouTubeChannelInput.parse("https://www.youtube.com/feeds/videos.xml?channel_id=UCfeed123")

    #expect(candidate.canonicalID == "UCfeed123")
    #expect(candidate.displayName == "YouTube UCfeed123")
}

@Test
func rejectsUnsupportedYouTubeInput() {
    #expect(throws: YouTubeChannelInputError.self) {
        try YouTubeChannelInput.parse("https://example.com/channel/UCabc")
    }
    #expect(throws: YouTubeChannelInputError.self) {
        try YouTubeChannelInput.parse("")
    }
}
