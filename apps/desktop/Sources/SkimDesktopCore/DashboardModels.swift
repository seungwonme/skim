import Foundation

public struct DashboardPost: Identifiable, Equatable, Sendable {
    public let id: Int64
    public let platform: String
    public let source: String?
    public let externalID: String?
    public let author: String
    public let title: String?
    public let content: String
    public let url: URL?
    public let timestamp: String?
    public let likes: Int?
    public let comments: Int?
    public let summary: String?
    public let contentMarkdown: String?
    public let wordCount: Int?
    public let crawledAt: String
    /// 크롤러가 extra JSON에 남긴 첨부/대표 이미지 CDN URL (SNS images + og:image)
    public let imageURLs: [String]

    public init(
        id: Int64,
        platform: String,
        source: String?,
        externalID: String? = nil,
        author: String,
        title: String?,
        content: String,
        url: URL?,
        timestamp: String?,
        likes: Int? = nil,
        comments: Int? = nil,
        summary: String? = nil,
        contentMarkdown: String? = nil,
        wordCount: Int? = nil,
        crawledAt: String,
        imageURLs: [String] = []
    ) {
        self.id = id
        self.platform = platform
        self.source = source
        self.externalID = externalID
        self.author = author
        self.title = title
        self.content = content
        self.url = url
        self.timestamp = timestamp
        self.likes = likes
        self.comments = comments
        self.summary = summary
        self.contentMarkdown = contentMarkdown
        self.wordCount = wordCount
        self.crawledAt = crawledAt
        self.imageURLs = imageURLs
    }

    public var displayTitle: String {
        guard let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return author
        }
        return title
    }
}

public struct DashboardSummary: Equatable, Sendable {
    public let postsCount: Int
    public let sourcesCount: Int
    public let credentialsCount: Int

    public init(postsCount: Int, sourcesCount: Int, credentialsCount: Int) {
        self.postsCount = postsCount
        self.sourcesCount = sourcesCount
        self.credentialsCount = credentialsCount
    }
}

public struct DashboardSnapshot: Equatable, Sendable {
    public let summary: DashboardSummary
    public let posts: [DashboardPost]
    public let sources: [TrackedSource]
    public let credentials: [PlatformCredential]
    public let databasePath: String

    public init(
        summary: DashboardSummary,
        posts: [DashboardPost],
        sources: [TrackedSource],
        credentials: [PlatformCredential],
        databasePath: String
    ) {
        self.summary = summary
        self.posts = posts
        self.sources = sources
        self.credentials = credentials
        self.databasePath = databasePath
    }

    public static let empty = DashboardSnapshot(
        summary: DashboardSummary(postsCount: 0, sourcesCount: 0, credentialsCount: 0),
        posts: [],
        sources: [],
        credentials: [],
        databasePath: ""
    )
}

public struct TrackedSource: Identifiable, Equatable, Sendable {
    public let id: Int64
    public let platform: String
    public let sourceType: String
    public let displayName: String
    public let canonicalID: String
    public let handleOrURL: String?
    public let isEnabled: Bool
    public let focusLevel: Int
    public let notes: String?
    public let createdAt: String
    public let updatedAt: String

    public init(
        id: Int64,
        platform: String,
        sourceType: String,
        displayName: String,
        canonicalID: String,
        handleOrURL: String?,
        isEnabled: Bool,
        focusLevel: Int,
        notes: String?,
        createdAt: String,
        updatedAt: String
    ) {
        self.id = id
        self.platform = platform
        self.sourceType = sourceType
        self.displayName = displayName
        self.canonicalID = canonicalID
        self.handleOrURL = handleOrURL
        self.isEnabled = isEnabled
        self.focusLevel = focusLevel
        self.notes = notes
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }
}

public struct TrackedSourceDraft: Equatable, Sendable {
    public let platform: String
    public let sourceType: String
    public let displayName: String
    public let canonicalID: String
    public let handleOrURL: String?
    public let isEnabled: Bool
    public let focusLevel: Int
    public let notes: String?

    public init(
        platform: String,
        sourceType: String,
        displayName: String,
        canonicalID: String,
        handleOrURL: String?,
        isEnabled: Bool = true,
        focusLevel: Int = 0,
        notes: String? = nil
    ) {
        self.platform = platform
        self.sourceType = sourceType
        self.displayName = displayName
        self.canonicalID = canonicalID
        self.handleOrURL = handleOrURL
        self.isEnabled = isEnabled
        self.focusLevel = focusLevel
        self.notes = notes
    }
}

public struct PlatformCredential: Identifiable, Equatable, Sendable {
    public let id: Int64
    public let platform: String
    public let accountLabel: String
    public let loginIdentifier: String
    public let secretService: String
    public let secretAccount: String
    public let sessionPath: String?
    public let sessionStatus: String
    public let lastVerifiedAt: String?
    public let createdAt: String
    public let updatedAt: String

    public init(
        id: Int64,
        platform: String,
        accountLabel: String,
        loginIdentifier: String,
        secretService: String,
        secretAccount: String,
        sessionPath: String?,
        sessionStatus: String,
        lastVerifiedAt: String?,
        createdAt: String,
        updatedAt: String
    ) {
        self.id = id
        self.platform = platform
        self.accountLabel = accountLabel
        self.loginIdentifier = loginIdentifier
        self.secretService = secretService
        self.secretAccount = secretAccount
        self.sessionPath = sessionPath
        self.sessionStatus = sessionStatus
        self.lastVerifiedAt = lastVerifiedAt
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }
}

public struct PlatformCredentialDraft: Equatable, Sendable {
    public let id: Int64?
    public let platform: String
    public let accountLabel: String
    public let loginIdentifier: String
    public let password: String?

    public init(
        id: Int64? = nil,
        platform: String,
        accountLabel: String,
        loginIdentifier: String,
        password: String? = nil
    ) {
        self.id = id
        self.platform = platform
        self.accountLabel = accountLabel
        self.loginIdentifier = loginIdentifier
        self.password = password
    }
}

public enum WorkspaceLocator {
    /// 로컬 빌드 시점의 리포 루트. #filePath는
    /// <repo>/apps/desktop/Sources/SkimDesktopCore/DashboardModels.swift 를 가리킨다.
    private static var sourceRepoRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent() // SkimDesktopCore
            .deletingLastPathComponent() // Sources
            .deletingLastPathComponent() // desktop
            .deletingLastPathComponent() // apps
            .deletingLastPathComponent() // repo root
    }

    public static func workspaceRoot(from currentDirectory: URL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)) -> URL {
        if let override = ProcessInfo.processInfo.environment["SKIM_WORKSPACE_ROOT"], !override.isEmpty {
            return URL(fileURLWithPath: override, isDirectory: true)
        }

        var candidate = currentDirectory
        for _ in 0..<8 {
            if FileManager.default.fileExists(atPath: candidate.appending(path: "packages/skim-core").path) {
                return candidate
            }
            let parent = candidate.deletingLastPathComponent()
            if parent.path == candidate.path {
                break
            }
            candidate = parent
        }

        // cwd 상위에서 마커를 못 찾으면 빌드된 소스 위치의 리포 루트로 폴백한다.
        // 무관한 폴더에서 실행했을 때 그 자리에 빈 data/skim.db를 만들어버리는 것을 막는다.
        if FileManager.default.fileExists(atPath: sourceRepoRoot.appending(path: "packages/skim-core").path) {
            return sourceRepoRoot
        }
        return currentDirectory
    }

    public static func defaultDatabasePath(from currentDirectory: URL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)) -> URL {
        workspaceRoot(from: currentDirectory).appending(path: "data/skim.db")
    }
}
