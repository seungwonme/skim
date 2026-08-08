import Foundation
import SkimDesktopCore

let arguments = Set(CommandLine.arguments.dropFirst())
let fixtureMode = arguments.contains("--fixture")
let path = fixtureMode
    ? FileManager.default.temporaryDirectory
        .appending(path: "skim-desktop-smoke-\(UUID().uuidString)")
        .appending(path: "fixture.db")
    : WorkspaceLocator.defaultDatabasePath()

let database = try SkimDatabase(path: path)
try database.ensureSchema()

print("SkimDesktopSmoke mode=\(fixtureMode ? "fixture" : "workspace")")
print("database=\(path.path)")
print("summary=\(try database.fetchSummary())")
let recentPosts = try database.fetchRecentPosts(limit: 60)
print("recent_posts=\(recentPosts.count)")
print("posts_with_images=\(recentPosts.filter { !$0.imageURLs.isEmpty }.count)")
print("tracked_sources=\(try database.fetchTrackedSources().count)")

// 사이드바 배지와 플랫폼 필터는 로드된 페이지가 아니라 DB 전체를 기준으로 한다.
let counts = try database.countsByPlatform()
print("platform_counts=\(counts.map { "\($0.name):\($0.count)" }.joined(separator: ","))")
for entry in counts.prefix(3) {
    let filtered = try database.fetchRecentPosts(limit: 5, platform: entry.name)
    let total = try database.countPosts(platform: entry.name)
    print("filter[\(entry.name)] total=\(total) head=\(filtered.count) pure=\(filtered.allSatisfy { $0.platform == entry.name })")
}
