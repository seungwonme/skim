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
    dependencies: [
        .package(url: "https://github.com/johnxnguyen/Down.git", from: "0.9.5")
    ],
    targets: [
        .target(
            name: "SkimDesktopCore",
            linkerSettings: [
                .linkedLibrary("sqlite3"),
                .linkedFramework("Security")
            ]
        ),
        .executableTarget(
            name: "SkimDesktopApp",
            dependencies: [
                "SkimDesktopCore",
                .product(name: "Down", package: "Down")
            ]
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
