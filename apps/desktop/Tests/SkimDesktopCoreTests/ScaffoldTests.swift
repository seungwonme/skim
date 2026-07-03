import Foundation
import Testing

@testable import SkimDesktopCore

@Test
func workspaceRootResolvesMarkerAndEscapesWorktree() throws {
    let fm = FileManager.default
    let base = fm.temporaryDirectory.appending(path: "skim-locator-\(UUID().uuidString)")
    let repo = base.appending(path: "repo")
    let worktree = repo.appending(path: ".claude/worktrees/wt")
    for root in [repo, worktree] {
        try fm.createDirectory(
            at: root.appending(path: "packages/skim-core"), withIntermediateDirectories: true)
    }
    defer { try? fm.removeItem(at: base) }

    #expect(WorkspaceLocator.defaultDatabasePath(from: repo).path == repo.appending(path: "data/skim.db").path)
    // worktree 안에서 실행해도 실제 repo root의 DB를 잡는다.
    #expect(WorkspaceLocator.workspaceRoot(from: worktree).path == repo.path)
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
    #expect(post.displayTitle == "Author")
    #expect(post.url?.host() == "www.youtube.com")
}
