// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "SkimSwiftDesktop",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "SkimDesktop", targets: ["SkimDesktopApp"]),
        .executable(name: "SkimDesktopSmoke", targets: ["SkimDesktopSmoke"]),
        .library(name: "SkimDesktopCore", targets: ["SkimDesktopCore"])
    ],
    targets: [
        .target(name: "SkimDesktopCore"),
        .executableTarget(
            name: "SkimDesktopApp",
            dependencies: ["SkimDesktopCore"]
        ),
        .executableTarget(
            name: "SkimDesktopSmoke",
            dependencies: ["SkimDesktopCore"]
        ),
        .testTarget(
            name: "SkimDesktopCoreTests",
            dependencies: ["SkimDesktopCore"]
        )
    ]
)
