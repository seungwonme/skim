import Foundation
import Testing

@testable import SkimDesktopCore

@Test
func defaultDatabasePathUsesWorkspaceDataDirectory() {
    let root = URL(fileURLWithPath: "/tmp/skim-workspace", isDirectory: true)
    #expect(WorkspaceLocator.defaultDatabasePath(from: root).path == "/tmp/skim-workspace/data/skim.db")
}

@Test
func dashboardPostKeepsReadableTitleFallbackInputs() {
    let post = DashboardPost(
        id: 7,
        platform: "youtube",
        source: "Source",
        author: "Author",
        title: nil,
        content: "Content",
        url: URL(string: "https://www.youtube.com/watch?v=abc123"),
        timestamp: nil,
        crawledAt: "now"
    )

    #expect(post.id == 7)
    #expect(post.title == nil)
    #expect(post.url?.host() == "www.youtube.com")
}
