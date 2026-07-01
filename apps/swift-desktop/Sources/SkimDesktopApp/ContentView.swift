import AppKit
import SkimDesktopCore
import SwiftUI

struct ContentView: View {
    @State private var snapshot = DashboardSnapshot.empty
    @State private var selectedPostID: DashboardPost.ID?
    @State private var loadError: String?
    @State private var youtubeInput = ""
    @State private var sourceMessage: Notice?
    @State private var searchText = ""
    @State private var platformFilter: String?

    private var filteredPosts: [DashboardPost] {
        snapshot.posts.filter { post in
            let platformMatches = platformFilter == nil || post.platform == platformFilter
            let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            guard platformMatches, !query.isEmpty else {
                return platformMatches
            }

            return [
                post.displayTitle,
                post.source ?? "",
                post.author,
                post.summary ?? "",
                post.content
            ]
            .joined(separator: " ")
            .lowercased()
            .contains(query)
        }
    }

    private var selectedPost: DashboardPost? {
        filteredPosts.first { $0.id == selectedPostID }
            ?? filteredPosts.first
            ?? snapshot.posts.first
    }

    private var platforms: [String] {
        Array(Set(snapshot.posts.map(\.platform))).sorted()
    }

    var body: some View {
        HSplitView {
            libraryPane
                .frame(minWidth: 286, idealWidth: 318, maxWidth: 360)

            feedPane
                .frame(minWidth: 390, idealWidth: 460)

            readerPane
                .frame(minWidth: 520, idealWidth: 680)
        }
        .background(Design.windowBackground)
        .frame(minWidth: 1240, minHeight: 760)
        .task {
            loadDashboard()
        }
    }

    private var libraryPane: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Skim")
                    .font(.system(size: 28, weight: .bold, design: .serif))
                Text("Local signal desk")
                    .font(.callout.weight(.medium))
                    .foregroundStyle(.secondary)
            }

            VStack(spacing: 10) {
                metricTile(symbol: "doc.text", title: "Posts", value: snapshot.summary.postsCount.formatted())
                metricTile(symbol: "antenna.radiowaves.left.and.right", title: "Sources", value: snapshot.summary.sourcesCount.formatted())
                metricTile(symbol: "line.3.horizontal.decrease.circle", title: "Showing", value: filteredPosts.count.formatted())
            }

            VStack(alignment: .leading, spacing: 10) {
                sectionLabel("Subscribe")
                HStack(spacing: 8) {
                    TextField("youtube.com/@channel", text: $youtubeInput)
                        .textFieldStyle(.plain)
                        .padding(.horizontal, 10)
                        .frame(height: 34)
                        .background(Design.inputBackground, in: RoundedRectangle(cornerRadius: 7))
                    Button {
                        addYouTubeSource()
                    } label: {
                        Image(systemName: "plus")
                            .frame(width: 30, height: 30)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(youtubeInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .help("Add YouTube source")
                }
                if let sourceMessage {
                    Text(sourceMessage.text)
                        .font(.caption)
                        .foregroundStyle(sourceMessage.isError ? Color.red : Design.green)
                        .lineLimit(2)
                }
            }

            VStack(alignment: .leading, spacing: 10) {
                sectionLabel("Sources")
                if snapshot.sources.isEmpty {
                    emptyLine("No tracked sources")
                } else {
                    ScrollView {
                        LazyVStack(spacing: 6) {
                            ForEach(snapshot.sources.prefix(18)) { source in
                                sourceRow(source)
                            }
                        }
                    }
                    .scrollIndicators(.hidden)
                }
            }

            Spacer(minLength: 8)

            VStack(alignment: .leading, spacing: 6) {
                Text(snapshot.databasePath.isEmpty ? "data/skim.db" : snapshot.databasePath)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .truncationMode(.middle)
                Button {
                    loadDashboard()
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
            }
        }
        .padding(22)
        .background(Design.sidebarBackground)
    }

    private var feedPane: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Inbox")
                        .font(.system(size: 22, weight: .semibold, design: .serif))
                    Text("\(filteredPosts.count.formatted()) of \(snapshot.posts.count.formatted()) loaded")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }

            TextField("Search title, source, summary", text: $searchText)
                .textFieldStyle(.plain)
                .padding(.horizontal, 12)
                .frame(height: 38)
                .background(Design.inputBackground, in: RoundedRectangle(cornerRadius: 7))

            platformBar

            if let loadError {
                ContentUnavailableView("Could not load data", systemImage: "exclamationmark.triangle", description: Text(loadError))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if filteredPosts.isEmpty {
                ContentUnavailableView("No matches", systemImage: "tray", description: Text("Adjust the search or platform filter."))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 10) {
                        ForEach(filteredPosts) { post in
                            feedCard(post)
                        }
                    }
                    .padding(.bottom, 18)
                }
                .scrollIndicators(.automatic)
            }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 20)
        .background(Design.feedBackground)
    }

    private var platformBar: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 6) {
                filterChip(title: "All", isSelected: platformFilter == nil) {
                    platformFilter = nil
                }
                ForEach(platforms, id: \.self) { platform in
                    filterChip(title: platform, isSelected: platformFilter == platform) {
                        platformFilter = platform
                    }
                }
            }
        }
        .scrollIndicators(.hidden)
    }

    private var readerPane: some View {
        Group {
            if let post = selectedPost {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 8) {
                                HStack(spacing: 8) {
                                    platformBadge(post.platform)
                                    Text(post.source ?? post.author)
                                        .font(.caption.weight(.medium))
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                }
                                Text(post.displayTitle)
                                    .font(.system(size: 30, weight: .semibold, design: .serif))
                                    .lineSpacing(2)
                                    .textSelection(.enabled)
                            }
                            Spacer()
                            if let url = post.url {
                                Link(destination: url) {
                                    Label("Open", systemImage: "arrow.up.right")
                                }
                                .buttonStyle(.bordered)
                            }
                        }

                        infoStrip(post)

                        if let url = post.url {
                            PreviewPane(preview: ContentPreview.classify(url))
                        }

                        VStack(alignment: .leading, spacing: 10) {
                            sectionLabel(post.summary == nil ? "Content" : "Summary")
                            Text(post.summary ?? post.content)
                                .font(.system(size: 15))
                                .foregroundStyle(Design.readerText)
                                .lineSpacing(5)
                                .textSelection(.enabled)
                        }

                        if let markdown = post.contentMarkdown, !markdown.isEmpty {
                            VStack(alignment: .leading, spacing: 10) {
                                sectionLabel("Markdown")
                                Text(markdown)
                                    .font(.system(.body, design: .monospaced))
                                    .foregroundStyle(.secondary)
                                    .lineSpacing(4)
                                    .textSelection(.enabled)
                            }
                        }
                    }
                    .padding(30)
                    .frame(maxWidth: 820, alignment: .leading)
                }
                .background(Design.readerBackground)
            } else {
                ContentUnavailableView("Select a post", systemImage: "sidebar.right")
                    .background(Design.readerBackground)
            }
        }
    }

    private func metricTile(symbol: String, title: String, value: String) -> some View {
        HStack(spacing: 11) {
            Image(systemName: symbol)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Design.green)
                .frame(width: 28, height: 28)
                .background(Design.green.opacity(0.12), in: RoundedRectangle(cornerRadius: 7))
            VStack(alignment: .leading, spacing: 2) {
                Text(title.uppercased())
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.secondary)
                Text(value)
                    .font(.title3.weight(.semibold))
            }
            Spacer()
        }
        .padding(12)
        .background(Design.panelBackground, in: RoundedRectangle(cornerRadius: 8))
    }

    private func sourceRow(_ source: TrackedSource) -> some View {
        HStack(spacing: 10) {
            Circle()
                .fill(platformColor(source.platform))
                .frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 2) {
                Text(source.displayName)
                    .font(.callout.weight(.medium))
                    .lineLimit(1)
                Text("\(source.platform) / \(source.sourceType)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            if source.focusLevel > 0 {
                Text(source.focusLevel.formatted())
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(Design.amber)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(Design.panelBackground.opacity(0.72), in: RoundedRectangle(cornerRadius: 8))
    }

    private func feedCard(_ post: DashboardPost) -> some View {
        Button {
            selectedPostID = post.id
        } label: {
            HStack(alignment: .top, spacing: 12) {
                RoundedRectangle(cornerRadius: 2)
                    .fill(platformColor(post.platform))
                    .frame(width: 4)

                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) {
                        platformBadge(post.platform)
                        Text(displayDate(post))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Spacer()
                    }

                    Text(post.displayTitle)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Design.primaryText)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)

                    Text(post.summary ?? post.content)
                        .font(.system(size: 13))
                        .foregroundStyle(.secondary)
                        .lineLimit(3)
                        .multilineTextAlignment(.leading)

                    HStack(spacing: 10) {
                        Text(post.source ?? post.author)
                            .lineLimit(1)
                        if let likes = post.likes {
                            Label(likes.formatted(), systemImage: "hand.thumbsup")
                                .labelStyle(.titleAndIcon)
                        }
                        if let comments = post.comments {
                            Label(comments.formatted(), systemImage: "text.bubble")
                                .labelStyle(.titleAndIcon)
                        }
                    }
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(selectedPostID == post.id ? Design.selectedBackground : Design.cardBackground, in: RoundedRectangle(cornerRadius: 8))
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .stroke(selectedPostID == post.id ? Design.green.opacity(0.5) : Design.hairline, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }

    private func filterChip(title: String, isSelected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(isSelected ? Color.white : Design.primaryText)
                .padding(.horizontal, 10)
                .frame(height: 28)
                .background(isSelected ? Design.green : Design.inputBackground, in: Capsule())
        }
        .buttonStyle(.plain)
    }

    private func platformBadge(_ platform: String) -> some View {
        Text(platform.uppercased())
            .font(.caption2.weight(.bold))
            .foregroundStyle(platformColor(platform))
            .padding(.horizontal, 7)
            .frame(height: 20)
            .background(platformColor(platform).opacity(0.12), in: Capsule())
    }

    private func infoStrip(_ post: DashboardPost) -> some View {
        HStack(spacing: 12) {
            infoItem("Author", post.author)
            if let source = post.source {
                infoItem("Source", source)
            }
            infoItem("Date", displayDate(post))
            if let likes = post.likes {
                infoItem("Likes", likes.formatted())
            }
            if let comments = post.comments {
                infoItem("Comments", comments.formatted())
            }
        }
        .padding(12)
        .background(Design.panelBackground, in: RoundedRectangle(cornerRadius: 8))
    }

    private func infoItem(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title.uppercased())
                .font(.caption2.weight(.bold))
                .foregroundStyle(.tertiary)
            Text(value)
                .font(.caption.weight(.medium))
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func sectionLabel(_ title: String) -> some View {
        Text(title.uppercased())
            .font(.caption2.weight(.bold))
            .foregroundStyle(.secondary)
    }

    private func emptyLine(_ title: String) -> some View {
        Text(title)
            .font(.callout)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(Design.panelBackground.opacity(0.7), in: RoundedRectangle(cornerRadius: 8))
    }

    private func displayDate(_ post: DashboardPost) -> String {
        let raw = post.timestamp ?? post.crawledAt
        return raw
            .replacingOccurrences(of: "T", with: " ")
            .replacingOccurrences(of: "+09:00", with: "")
            .prefix(16)
            .description
    }

    private func platformColor(_ platform: String) -> Color {
        switch platform {
        case "youtube": Design.red
        case "threads": Design.green
        case "reddit": Design.orange
        case "huggingface": Design.amber
        case "arxiv": Design.plum
        case "linkedin": Design.blue
        case "x": Design.primaryText
        default: Design.teal
        }
    }

    private func loadDashboard() {
        do {
            let database = try SkimDatabase(path: WorkspaceLocator.defaultDatabasePath())
            try database.ensureSchema()
            snapshot = try database.loadDashboard(limit: 180)
            if let selectedPostID, snapshot.posts.contains(where: { $0.id == selectedPostID }) {
                self.selectedPostID = selectedPostID
            } else {
                selectedPostID = snapshot.posts.first?.id
            }
            loadError = nil
        } catch {
            loadError = String(describing: error)
        }
    }

    private func addYouTubeSource() {
        do {
            let candidate = try YouTubeChannelInput.parse(youtubeInput)
            let database = try SkimDatabase(path: WorkspaceLocator.defaultDatabasePath())
            try database.ensureSchema()
            let source = try database.upsertTrackedSource(candidate.draft)
            youtubeInput = ""
            sourceMessage = Notice(text: "Added \(source.displayName)", isError: false)
            loadDashboard()
        } catch {
            sourceMessage = Notice(text: String(describing: error), isError: true)
        }
    }
}

private struct Notice: Equatable {
    let text: String
    let isError: Bool
}

private enum Design {
    static let windowBackground = Color(nsColor: NSColor.windowBackgroundColor)
    static let sidebarBackground = Color(nsColor: NSColor.controlBackgroundColor)
    static let feedBackground = Color(nsColor: NSColor.textBackgroundColor).opacity(0.96)
    static let readerBackground = Color(nsColor: NSColor.windowBackgroundColor)
    static let panelBackground = Color(nsColor: NSColor.textBackgroundColor)
    static let cardBackground = Color(nsColor: NSColor.textBackgroundColor)
    static let inputBackground = Color(nsColor: NSColor.controlBackgroundColor)
    static let selectedBackground = dynamicColor(
        light: NSColor(calibratedRed: 0.88, green: 0.95, blue: 0.90, alpha: 1),
        dark: NSColor(calibratedRed: 0.08, green: 0.22, blue: 0.16, alpha: 1)
    )
    static let hairline = Color.black.opacity(0.08)
    static let primaryText = Color(nsColor: NSColor.labelColor)
    static let readerText = Color(nsColor: NSColor.labelColor).opacity(0.92)
    static let green = dynamicColor(
        light: NSColor(calibratedRed: 0.12, green: 0.45, blue: 0.29, alpha: 1),
        dark: NSColor(calibratedRed: 0.25, green: 0.74, blue: 0.48, alpha: 1)
    )
    static let red = Color(red: 0.74, green: 0.12, blue: 0.10)
    static let orange = Color(red: 0.82, green: 0.34, blue: 0.11)
    static let amber = Color(red: 0.70, green: 0.48, blue: 0.08)
    static let plum = Color(red: 0.47, green: 0.22, blue: 0.52)
    static let blue = Color(red: 0.13, green: 0.35, blue: 0.62)
    static let teal = Color(red: 0.10, green: 0.45, blue: 0.47)

    private static func dynamicColor(light: NSColor, dark: NSColor) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light
        })
    }
}
