import Foundation
import SQLite3

public enum SkimDatabaseError: Error, LocalizedError, Sendable {
    case openFailed(String)
    case executeFailed(String)
    case prepareFailed(String)
    case stepFailed(String)
    case missingHandle
    case keychainFailed(status: OSStatus)
    case invalidCredential(String)

    public var errorDescription: String? {
        switch self {
        case let .openFailed(message): "SQLite 데이터베이스를 열 수 없습니다: \(message)"
        case let .executeFailed(message): "SQLite 실행에 실패했습니다: \(message)"
        case let .prepareFailed(message): "SQLite 쿼리 준비에 실패했습니다: \(message)"
        case let .stepFailed(message): "SQLite 쿼리 처리에 실패했습니다: \(message)"
        case .missingHandle: "SQLite 연결을 사용할 수 없습니다."
        case let .keychainFailed(status): "macOS 키체인 작업에 실패했습니다. 상태 코드: \(status)"
        case let .invalidCredential(message): message
        }
    }
}

public final class SkimDatabase {
    private var handle: OpaquePointer?
    private let path: URL

    public init(path: URL, createIfMissing: Bool = true) throws {
        self.path = path
        if createIfMissing {
            try FileManager.default.createDirectory(
                at: path.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
        }

        let flags = createIfMissing
            ? SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX
            : SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX
        var database: OpaquePointer?
        guard sqlite3_open_v2(path.path, &database, flags, nil) == SQLITE_OK else {
            let message = database.map { String(cString: sqlite3_errmsg($0)) } ?? "unknown"
            if let database {
                sqlite3_close(database)
            }
            throw SkimDatabaseError.openFailed(message)
        }
        handle = database
    }

    deinit {
        if let handle {
            sqlite3_close(handle)
        }
    }

    public func ensureSchema() throws {
        try execute(Self.schemaSQL)
    }

    public func fetchSummary() throws -> DashboardSummary {
        DashboardSummary(
            postsCount: try countRows(table: "posts"),
            sourcesCount: try countRows(table: "tracked_sources"),
            credentialsCount: try countRows(table: "platform_credentials")
        )
    }

    public func loadDashboard(limit: Int = 80) throws -> DashboardSnapshot {
        DashboardSnapshot(
            summary: try fetchSummary(),
            posts: try fetchRecentPosts(limit: limit),
            sources: try fetchTrackedSources(),
            credentials: try fetchCredentials(),
            databasePath: path.path
        )
    }

    public func fetchCredentials() throws -> [PlatformCredential] {
        try query(
            """
            SELECT id, platform, account_label, login_identifier, secret_service, secret_account,
                   session_path, session_status, last_verified_at, created_at, updated_at
            FROM platform_credentials
            ORDER BY platform, account_label COLLATE NOCASE ASC
            """
        ) { statement in
            PlatformCredential(
                id: sqlite3_column_int64(statement, 0),
                platform: text(statement, 1) ?? "",
                accountLabel: text(statement, 2) ?? "",
                loginIdentifier: text(statement, 3) ?? "",
                secretService: text(statement, 4) ?? "",
                secretAccount: text(statement, 5) ?? "",
                sessionPath: text(statement, 6),
                sessionStatus: text(statement, 7) ?? "missing",
                lastVerifiedAt: text(statement, 8),
                createdAt: text(statement, 9) ?? "",
                updatedAt: text(statement, 10) ?? ""
            )
        }
    }

    public func fetchTrackedSources() throws -> [TrackedSource] {
        try query(
            """
            SELECT id, platform, source_type, display_name, canonical_id, handle_or_url,
                   is_enabled, focus_level, notes, created_at, updated_at
            FROM tracked_sources
            ORDER BY platform, focus_level DESC, display_name COLLATE NOCASE ASC
            """
        ) { statement in
            TrackedSource(
                id: sqlite3_column_int64(statement, 0),
                platform: text(statement, 1) ?? "",
                sourceType: text(statement, 2) ?? "",
                displayName: text(statement, 3) ?? "",
                canonicalID: text(statement, 4) ?? "",
                handleOrURL: text(statement, 5),
                isEnabled: sqlite3_column_int(statement, 6) != 0,
                focusLevel: Int(sqlite3_column_int64(statement, 7)),
                notes: text(statement, 8),
                createdAt: text(statement, 9) ?? "",
                updatedAt: text(statement, 10) ?? ""
            )
        }
    }

    public func fetchRecentPosts(limit: Int = 50) throws -> [DashboardPost] {
        try query(
            """
            SELECT id, platform, source, external_id, author, title, content, url, timestamp,
                   likes, comments, summary, content_markdown, word_count, crawled_at
            FROM posts
            ORDER BY datetime(COALESCE(NULLIF(timestamp, ''), crawled_at)) DESC, id DESC
            LIMIT ?
            """,
            bindings: [.integer(Int64(max(1, limit)))]
        ) { statement in
            DashboardPost(
                id: sqlite3_column_int64(statement, 0),
                platform: text(statement, 1) ?? "",
                source: text(statement, 2),
                externalID: text(statement, 3),
                author: text(statement, 4) ?? "",
                title: text(statement, 5),
                content: text(statement, 6) ?? "",
                url: text(statement, 7).flatMap(URL.init(string:)),
                timestamp: text(statement, 8),
                likes: int(statement, 9),
                comments: int(statement, 10),
                summary: text(statement, 11),
                contentMarkdown: text(statement, 12),
                wordCount: int(statement, 13),
                crawledAt: text(statement, 14) ?? ""
            )
        }
    }

    @discardableResult
    public func upsertTrackedSource(_ draft: TrackedSourceDraft) throws -> TrackedSource {
        try execute(
            """
            INSERT INTO tracked_sources (
                platform, source_type, display_name, canonical_id, handle_or_url,
                is_enabled, focus_level, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, canonical_id) DO UPDATE SET
                source_type = excluded.source_type,
                display_name = excluded.display_name,
                handle_or_url = excluded.handle_or_url,
                is_enabled = excluded.is_enabled,
                focus_level = excluded.focus_level,
                notes = excluded.notes,
                updated_at = datetime('now')
            """,
            bindings: [
                .text(draft.platform),
                .text(draft.sourceType),
                .text(draft.displayName),
                .text(draft.canonicalID),
                .optionalText(draft.handleOrURL),
                .integer(draft.isEnabled ? 1 : 0),
                .integer(Int64(draft.focusLevel)),
                .optionalText(draft.notes)
            ]
        )

        return try queryOne(
            """
            SELECT id, platform, source_type, display_name, canonical_id, handle_or_url,
                   is_enabled, focus_level, notes, created_at, updated_at
            FROM tracked_sources
            WHERE platform = ? AND canonical_id = ?
            """,
            bindings: [.text(draft.platform), .text(draft.canonicalID)]
        ) { statement in
            TrackedSource(
                id: sqlite3_column_int64(statement, 0),
                platform: text(statement, 1) ?? "",
                sourceType: text(statement, 2) ?? "",
                displayName: text(statement, 3) ?? "",
                canonicalID: text(statement, 4) ?? "",
                handleOrURL: text(statement, 5),
                isEnabled: sqlite3_column_int(statement, 6) != 0,
                focusLevel: Int(sqlite3_column_int64(statement, 7)),
                notes: text(statement, 8),
                createdAt: text(statement, 9) ?? "",
                updatedAt: text(statement, 10) ?? ""
            )
        }
    }

    @discardableResult
    public func saveCredential(_ draft: PlatformCredentialDraft, writeKeychain: Bool = true) throws -> PlatformCredential {
        let platform = draft.platform.trimmingCharacters(in: .whitespacesAndNewlines)
        let accountLabel = draft.accountLabel.trimmingCharacters(in: .whitespacesAndNewlines)
        let loginIdentifier = draft.loginIdentifier.trimmingCharacters(in: .whitespacesAndNewlines)
        let password = draft.password?.trimmingCharacters(in: .whitespacesAndNewlines)

        guard !platform.isEmpty, !accountLabel.isEmpty, !loginIdentifier.isEmpty else {
            throw SkimDatabaseError.invalidCredential("플랫폼, 계정 라벨, 로그인 식별자는 필수입니다.")
        }

        let service = KeychainStore.secretService(platform: platform)
        let sessionPath = "data/sessions/\(platform)_session.json"
        let existing = try draft.id.flatMap(fetchCredential(id:))
        let credentialChanged = existing.map { $0.platform != platform || $0.loginIdentifier != loginIdentifier } ?? true

        if let password, !password.isEmpty {
            if writeKeychain {
                try KeychainStore.save(password: password, service: service, account: loginIdentifier)
                if let existing, credentialChanged {
                    try? KeychainStore.delete(service: existing.secretService, account: existing.secretAccount)
                }
            }
        } else if credentialChanged {
            throw SkimDatabaseError.invalidCredential("새 크레덴셜을 만들거나 플랫폼/로그인 식별자를 바꾸려면 비밀번호가 필요합니다.")
        }

        if let id = draft.id {
            try execute(
                """
                UPDATE platform_credentials
                SET platform = ?,
                    account_label = ?,
                    login_identifier = ?,
                    secret_service = ?,
                    secret_account = ?,
                    session_path = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                bindings: [
                    .text(platform),
                    .text(accountLabel),
                    .text(loginIdentifier),
                    .text(service),
                    .text(loginIdentifier),
                    .text(sessionPath),
                    .integer(id)
                ]
            )
        } else {
            try execute(
                """
                INSERT INTO platform_credentials (
                    platform, account_label, login_identifier, secret_service, secret_account, session_path
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, login_identifier) DO UPDATE SET
                    account_label = excluded.account_label,
                    secret_service = excluded.secret_service,
                    secret_account = excluded.secret_account,
                    session_path = excluded.session_path,
                    updated_at = datetime('now')
                """,
                bindings: [
                    .text(platform),
                    .text(accountLabel),
                    .text(loginIdentifier),
                    .text(service),
                    .text(loginIdentifier),
                    .text(sessionPath)
                ]
            )
        }

        return try queryOne(
            """
            SELECT id, platform, account_label, login_identifier, secret_service, secret_account,
                   session_path, session_status, last_verified_at, created_at, updated_at
            FROM platform_credentials
            WHERE platform = ? AND login_identifier = ?
            """,
            bindings: [.text(platform), .text(loginIdentifier)]
        ) { statement in
            PlatformCredential(
                id: sqlite3_column_int64(statement, 0),
                platform: text(statement, 1) ?? "",
                accountLabel: text(statement, 2) ?? "",
                loginIdentifier: text(statement, 3) ?? "",
                secretService: text(statement, 4) ?? "",
                secretAccount: text(statement, 5) ?? "",
                sessionPath: text(statement, 6),
                sessionStatus: text(statement, 7) ?? "missing",
                lastVerifiedAt: text(statement, 8),
                createdAt: text(statement, 9) ?? "",
                updatedAt: text(statement, 10) ?? ""
            )
        }
    }

    public func deleteCredential(id: Int64, deleteKeychain: Bool = true) throws {
        guard let credential = try fetchCredential(id: id) else {
            return
        }
        if deleteKeychain {
            try KeychainStore.delete(service: credential.secretService, account: credential.secretAccount)
        }
        try execute("DELETE FROM platform_credentials WHERE id = ?", bindings: [.integer(id)])
    }

    func execute(_ sql: String, bindings: [SQLiteBinding] = []) throws {
        if bindings.isEmpty {
            guard sqlite3_exec(try requireHandle(), sql, nil, nil, nil) == SQLITE_OK else {
                throw SkimDatabaseError.executeFailed(errorMessage())
            }
            return
        }

        try withStatement(sql) { statement in
            try bind(bindings, to: statement)
            guard sqlite3_step(statement) == SQLITE_DONE else {
                throw SkimDatabaseError.stepFailed(errorMessage())
            }
        }
    }

    private func countRows(table: String) throws -> Int {
        try queryOne("SELECT COUNT(*) FROM \(table)") { statement in
            Int(sqlite3_column_int64(statement, 0))
        }
    }

    private func fetchCredential(id: Int64) throws -> PlatformCredential? {
        try query(
            """
            SELECT id, platform, account_label, login_identifier, secret_service, secret_account,
                   session_path, session_status, last_verified_at, created_at, updated_at
            FROM platform_credentials
            WHERE id = ?
            """,
            bindings: [.integer(id)]
        ) { statement in
            PlatformCredential(
                id: sqlite3_column_int64(statement, 0),
                platform: text(statement, 1) ?? "",
                accountLabel: text(statement, 2) ?? "",
                loginIdentifier: text(statement, 3) ?? "",
                secretService: text(statement, 4) ?? "",
                secretAccount: text(statement, 5) ?? "",
                sessionPath: text(statement, 6),
                sessionStatus: text(statement, 7) ?? "missing",
                lastVerifiedAt: text(statement, 8),
                createdAt: text(statement, 9) ?? "",
                updatedAt: text(statement, 10) ?? ""
            )
        }
        .first
    }

    private func query<T>(
        _ sql: String,
        bindings: [SQLiteBinding] = [],
        map: (OpaquePointer) throws -> T
    ) throws -> [T] {
        try withStatement(sql) { statement in
            try bind(bindings, to: statement)
            var rows: [T] = []
            while true {
                let result = sqlite3_step(statement)
                if result == SQLITE_ROW {
                    rows.append(try map(statement))
                } else if result == SQLITE_DONE {
                    return rows
                } else {
                    throw SkimDatabaseError.stepFailed(errorMessage())
                }
            }
        }
    }

    private func queryOne<T>(
        _ sql: String,
        bindings: [SQLiteBinding] = [],
        map: (OpaquePointer) throws -> T
    ) throws -> T {
        let rows = try query(sql, bindings: bindings, map: map)
        guard let first = rows.first else {
            throw SkimDatabaseError.stepFailed("query returned no rows")
        }
        return first
    }

    private func withStatement<T>(_ sql: String, body: (OpaquePointer) throws -> T) throws -> T {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(try requireHandle(), sql, -1, &statement, nil) == SQLITE_OK,
              let statement
        else {
            throw SkimDatabaseError.prepareFailed(errorMessage())
        }
        defer {
            sqlite3_finalize(statement)
        }
        return try body(statement)
    }

    private func bind(_ bindings: [SQLiteBinding], to statement: OpaquePointer) throws {
        for (offset, binding) in bindings.enumerated() {
            let index = Int32(offset + 1)
            let result: Int32
            switch binding {
            case let .integer(value):
                result = sqlite3_bind_int64(statement, index, value)
            case let .text(value):
                result = sqlite3_bind_text(statement, index, value, -1, sqliteTransient)
            case let .optionalText(value):
                if let value {
                    result = sqlite3_bind_text(statement, index, value, -1, sqliteTransient)
                } else {
                    result = sqlite3_bind_null(statement, index)
                }
            }

            guard result == SQLITE_OK else {
                throw SkimDatabaseError.executeFailed(errorMessage())
            }
        }
    }

    private func requireHandle() throws -> OpaquePointer {
        guard let handle else {
            throw SkimDatabaseError.missingHandle
        }
        return handle
    }

    private func errorMessage() -> String {
        guard let handle else {
            return "missing database handle"
        }
        return String(cString: sqlite3_errmsg(handle))
    }

    private static let schemaSQL = """
    CREATE TABLE IF NOT EXISTS posts (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        platform         TEXT NOT NULL,
        source           TEXT,
        external_id      TEXT,
        author           TEXT NOT NULL,
        title            TEXT,
        content          TEXT NOT NULL,
        url              TEXT,
        timestamp        TEXT,
        likes            INTEGER,
        comments         INTEGER,
        reposts          INTEGER,
        views            INTEGER,
        summary          TEXT,
        content_markdown TEXT,
        word_count       INTEGER,
        extra            TEXT,
        crawled_at       TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(platform, external_id)
    );

    CREATE TABLE IF NOT EXISTS tracked_sources (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        platform      TEXT NOT NULL,
        source_type   TEXT NOT NULL,
        display_name  TEXT NOT NULL,
        canonical_id  TEXT NOT NULL,
        handle_or_url TEXT,
        is_enabled    INTEGER NOT NULL DEFAULT 1,
        focus_level   INTEGER NOT NULL DEFAULT 0,
        notes         TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(platform, canonical_id)
    );

    CREATE TABLE IF NOT EXISTS platform_credentials (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        platform         TEXT NOT NULL,
        account_label    TEXT NOT NULL,
        login_identifier TEXT NOT NULL,
        secret_service   TEXT NOT NULL,
        secret_account   TEXT NOT NULL,
        session_path     TEXT,
        session_status   TEXT NOT NULL DEFAULT 'missing',
        last_verified_at TEXT,
        created_at       TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(platform, login_identifier)
    );

    CREATE INDEX IF NOT EXISTS idx_posts_platform ON posts(platform);
    CREATE INDEX IF NOT EXISTS idx_posts_crawled_at ON posts(crawled_at);
    CREATE INDEX IF NOT EXISTS idx_tracked_sources_platform ON tracked_sources(platform);
    CREATE INDEX IF NOT EXISTS idx_tracked_sources_enabled ON tracked_sources(is_enabled);
    CREATE INDEX IF NOT EXISTS idx_credentials_platform ON platform_credentials(platform);
    """
}

enum SQLiteBinding {
    case integer(Int64)
    case text(String)
    case optionalText(String?)
}

private let sqliteTransient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

private func text(_ statement: OpaquePointer, _ index: Int32) -> String? {
    guard let raw = sqlite3_column_text(statement, index) else {
        return nil
    }
    return String(cString: UnsafeRawPointer(raw).assumingMemoryBound(to: CChar.self))
}

private func int(_ statement: OpaquePointer, _ index: Int32) -> Int? {
    guard sqlite3_column_type(statement, index) != SQLITE_NULL else {
        return nil
    }
    return Int(sqlite3_column_int64(statement, index))
}
