import Foundation
import SkimDesktopCore

let arguments = Set(CommandLine.arguments.dropFirst())
let mode = arguments.contains("--fixture") ? "fixture" : "workspace"
let path = WorkspaceLocator.defaultDatabasePath().path

print("SkimDesktopSmoke mode=\(mode)")
print("default_database=\(path)")
print("scaffold=ok")
