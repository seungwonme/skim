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
print("recent_posts=\(try database.fetchRecentPosts(limit: 5).count)")
print("tracked_sources=\(try database.fetchTrackedSources().count)")
