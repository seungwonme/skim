import Foundation

public struct DashboardPost: Identifiable, Equatable, Sendable {
    public let id: Int64
    public let platform: String
    public let source: String?
    public let author: String
    public let title: String?
    public let content: String
    public let url: URL?
    public let timestamp: String?
    public let crawledAt: String

    public init(
        id: Int64,
        platform: String,
        source: String?,
        author: String,
        title: String?,
        content: String,
        url: URL?,
        timestamp: String?,
        crawledAt: String
    ) {
        self.id = id
        self.platform = platform
        self.source = source
        self.author = author
        self.title = title
        self.content = content
        self.url = url
        self.timestamp = timestamp
        self.crawledAt = crawledAt
    }
}

public struct DashboardSummary: Equatable, Sendable {
    public let postsCount: Int
    public let sourcesCount: Int

    public init(postsCount: Int, sourcesCount: Int) {
        self.postsCount = postsCount
        self.sourcesCount = sourcesCount
    }
}

public enum WorkspaceLocator {
    public static func defaultDatabasePath(from currentDirectory: URL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)) -> URL {
        currentDirectory.appending(path: "data/skim.db")
    }
}
