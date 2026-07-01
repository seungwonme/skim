import Foundation
import Testing

@testable import SkimDesktopCore

@Test
func databaseReadsFixturePostsAndSources() throws {
    try withFixtureDatabase { database in
        try database.execute(
            """
            INSERT INTO posts (
                platform, source, external_id, author, title, content, url,
                timestamp, likes, comments, summary, content_markdown, word_count, crawled_at
            ) VALUES (
                'youtube', 'AI Channel', 'video-1', 'AI Channel', 'Practical Agents',
                'A dense video about agent workflows.',
                'https://www.youtube.com/watch?v=abc123XYZ09',
                '2026-07-01T10:00:00+09:00', 42, 7, 'Agent summary',
                '# Markdown body', 128, '2026-07-01 01:00:00'
            );

            INSERT INTO tracked_sources (
                platform, source_type, display_name, canonical_id, handle_or_url, focus_level
            ) VALUES (
                'youtube', 'channel', 'AI Channel', 'UCabc123XYZ09', 'https://www.youtube.com/channel/UCabc123XYZ09', 3
            );
            """
        )

        let summary = try database.fetchSummary()
        let snapshot = try database.loadDashboard(limit: 10)
        let posts = try database.fetchRecentPosts(limit: 10)
        let sources = try database.fetchTrackedSources()

        #expect(summary.postsCount == 1)
        #expect(summary.sourcesCount == 1)
        #expect(summary.credentialsCount == 0)
        #expect(snapshot.summary == summary)
        #expect(snapshot.posts.count == 1)
        #expect(snapshot.sources.count == 1)
        #expect(snapshot.credentials.isEmpty)
        #expect(snapshot.databasePath.hasSuffix("fixture.db"))
        #expect(posts.map(\.displayTitle) == ["Practical Agents"])
        #expect(posts.first?.url?.absoluteString == "https://www.youtube.com/watch?v=abc123XYZ09")
        #expect(posts.first?.likes == 42)
        #expect(sources.first?.canonicalID == "UCabc123XYZ09")
        #expect(sources.first?.focusLevel == 3)
    }
}

@Test
func recentPostsUsePostTimestampBeforeCrawlBatchTime() throws {
    try withFixtureDatabase { database in
        try database.execute(
            """
            INSERT INTO posts (
                platform, source, external_id, author, title, content, timestamp, crawled_at
            ) VALUES
            ('youtube', 'Late crawl', 'old-post', 'Channel', 'Old post', 'old', '2026-06-30T09:00:00+09:00', '2026-07-01 12:00:00'),
            ('youtube', 'Early crawl', 'new-post', 'Channel', 'New post', 'new', '2026-07-01T10:00:00+09:00', '2026-07-01 01:00:00');
            """
        )

        #expect(try database.fetchRecentPosts(limit: 2).map(\.displayTitle) == ["New post", "Old post"])
    }
}

@Test
func trackedSourceUpsertUsesPlatformAndCanonicalID() throws {
    try withFixtureDatabase { database in
        let first = try database.upsertTrackedSource(
            TrackedSourceDraft(
                platform: "youtube",
                sourceType: "channel",
                displayName: "First Name",
                canonicalID: "UCduplicate",
                handleOrURL: "https://www.youtube.com/channel/UCduplicate",
                focusLevel: 1
            )
        )
        let second = try database.upsertTrackedSource(
            TrackedSourceDraft(
                platform: "youtube",
                sourceType: "channel",
                displayName: "Updated Name",
                canonicalID: "UCduplicate",
                handleOrURL: "https://www.youtube.com/@updated",
                focusLevel: 5,
                notes: "updated"
            )
        )

        let sources = try database.fetchTrackedSources()

        #expect(first.id == second.id)
        #expect(sources.count == 1)
        #expect(sources[0].displayName == "Updated Name")
        #expect(sources[0].handleOrURL == "https://www.youtube.com/@updated")
        #expect(sources[0].focusLevel == 5)
        #expect(sources[0].notes == "updated")
    }
}

@Test
func credentialMetadataCanBeCreatedEditedAndDeleted() throws {
    try withFixtureDatabase { database in
        let created = try database.saveCredential(
            PlatformCredentialDraft(
                platform: "threads",
                accountLabel: "Personal",
                loginIdentifier: "me@example.com",
                password: "not-used-in-test"
            ),
            writeKeychain: false
        )

        #expect(created.platform == "threads")
        #expect(created.secretService == "skim.desktop.threads")
        #expect(created.secretAccount == "me@example.com")
        #expect(created.sessionPath == "data/sessions/threads_session.json")
        #expect(try database.fetchSummary().credentialsCount == 1)

        let updated = try database.saveCredential(
            PlatformCredentialDraft(
                id: created.id,
                platform: "threads",
                accountLabel: "Personal Updated",
                loginIdentifier: "me@example.com"
            ),
            writeKeychain: false
        )

        #expect(updated.id == created.id)
        #expect(updated.accountLabel == "Personal Updated")
        #expect(try database.fetchCredentials().map(\.accountLabel) == ["Personal Updated"])

        try database.deleteCredential(id: created.id, deleteKeychain: false)
        #expect(try database.fetchCredentials().isEmpty)
    }
}

@Test
func credentialPlatformOrLoginChangeRequiresPassword() throws {
    try withFixtureDatabase { database in
        let created = try database.saveCredential(
            PlatformCredentialDraft(
                platform: "threads",
                accountLabel: "Personal",
                loginIdentifier: "me@example.com",
                password: "not-used-in-test"
            ),
            writeKeychain: false
        )

        #expect(throws: SkimDatabaseError.self) {
            try database.saveCredential(
                PlatformCredentialDraft(
                    id: created.id,
                    platform: "x",
                    accountLabel: "Personal",
                    loginIdentifier: "me@example.com"
                ),
                writeKeychain: false
            )
        }
    }
}

@Test
func parsedYouTubeChannelInputPersistsAsTrackedSource() throws {
    try withFixtureDatabase { database in
        let candidate = try YouTubeChannelInput.parse("https://www.youtube.com/@openai")
        let source = try database.upsertTrackedSource(candidate.draft)

        #expect(source.platform == "youtube")
        #expect(source.sourceType == "channel")
        #expect(source.canonicalID == "@openai")
        #expect(source.handleOrURL == "https://www.youtube.com/@openai")
    }
}

private func withFixtureDatabase(_ body: (SkimDatabase) throws -> Void) throws {
    let directory = FileManager.default.temporaryDirectory.appending(
        path: "skim-swift-desktop-\(UUID().uuidString)",
        directoryHint: .isDirectory
    )
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer {
        try? FileManager.default.removeItem(at: directory)
    }

    let database = try SkimDatabase(path: directory.appending(path: "fixture.db"))
    try database.ensureSchema()
    try body(database)
}
