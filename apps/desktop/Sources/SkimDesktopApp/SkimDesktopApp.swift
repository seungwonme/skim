import AppKit
import SwiftUI

@main
struct SkimDesktopApp: App {
    init() {
        let iconURL = Bundle.main.url(forResource: "SkimIcon", withExtension: "png")
            ?? Bundle.module.url(forResource: "SkimIcon", withExtension: "png")
        if let iconURL,
           let icon = NSImage(contentsOf: iconURL) {
            NSApplication.shared.applicationIconImage = icon
        }
        NSApplication.shared.setActivationPolicy(.regular)
        DispatchQueue.main.async {
            NSApplication.shared.activate(ignoringOtherApps: true)
        }
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        .windowStyle(.titleBar)
    }
}
