import SkimDesktopCore
import SwiftUI

struct ContentView: View {
    @State private var snapshot = DashboardSnapshot.empty
    @State private var selectedPostID: DashboardPost.ID?
    @State private var loadError: String?

    private var selectedPost: DashboardPost? {
        snapshot.posts.first { $0.id == selectedPostID } ?? snapshot.posts.first
    }

    var body: some View {
        NavigationSplitView {
            sidebar
        } detail: {
            detail
        }
        .frame(minWidth: 1080, minHeight: 700)
        .task {
            loadDashboard()
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            summaryGrid
            sourceStrip
            feedList
        }
        .padding(18)
        .navigationTitle("Skim")
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Signal Feed")
                .font(.title2.weight(.semibold))
            Text(snapshot.databasePath.isEmpty ? "data/skim.db" : snapshot.databasePath)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
        }
    }

    private var summaryGrid: some View {
        HStack(spacing: 10) {
            metricCard(title: "Posts", value: snapshot.summary.postsCount)
            metricCard(title: "Sources", value: snapshot.summary.sourcesCount)
            metricCard(title: "Loaded", value: snapshot.posts.count)
        }
    }

    private func metricCard(title: String, value: Int) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title.uppercased())
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(value.formatted())
                .font(.title3.weight(.semibold))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }

    private var sourceStrip: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Tracked Sources")
                .font(.headline)
            if snapshot.sources.isEmpty {
                Text("No tracked sources yet.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                ScrollView(.horizontal) {
                    HStack {
                        ForEach(snapshot.sources.prefix(12)) { source in
                            Text(source.displayName)
                                .font(.caption.weight(.medium))
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(.thinMaterial, in: Capsule())
                        }
                    }
                }
                .scrollIndicators(.hidden)
            }
        }
    }

    private var feedList: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Recent Content")
                    .font(.headline)
                Spacer()
                Button("Refresh") {
                    loadDashboard()
                }
            }

            if let loadError {
                ContentUnavailableView("Could not load data", systemImage: "exclamationmark.triangle", description: Text(loadError))
            } else if snapshot.posts.isEmpty {
                ContentUnavailableView("No posts yet", systemImage: "tray", description: Text("Run a crawl, then refresh this dashboard."))
            } else {
                List(snapshot.posts, selection: $selectedPostID) { post in
                    postRow(post)
                }
                .listStyle(.inset)
            }
        }
    }

    private func postRow(_ post: DashboardPost) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(post.platform.uppercased())
                    .font(.caption.weight(.semibold))
                if let source = post.source {
                    Text(source)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer()
                Text(post.timestamp ?? post.crawledAt)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Text(post.displayTitle)
                .font(.headline)
                .lineLimit(2)
            Text(post.summary ?? post.content)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(2)
        }
        .padding(.vertical, 8)
        .tag(post.id)
    }

    private var detail: some View {
        Group {
            if let post = selectedPost {
                VStack(alignment: .leading, spacing: 18) {
                    HStack(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(post.platform.uppercased())
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.secondary)
                            Text(post.displayTitle)
                                .font(.title.weight(.semibold))
                                .textSelection(.enabled)
                        }
                        Spacer()
                        if let url = post.url {
                            Link("Open", destination: url)
                        }
                    }

                    HStack(spacing: 12) {
                        if let source = post.source {
                            label("Source", source)
                        }
                        if let likes = post.likes {
                            label("Likes", likes.formatted())
                        }
                        if let comments = post.comments {
                            label("Comments", comments.formatted())
                        }
                    }

                    Text(post.summary ?? post.content)
                        .font(.body)
                        .lineSpacing(4)
                        .textSelection(.enabled)

                    Spacer()
                }
                .padding(28)
            } else {
                ContentUnavailableView("Select a post", systemImage: "sidebar.left")
            }
        }
    }

    private func label(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title.uppercased())
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
            Text(value)
                .font(.callout.weight(.medium))
        }
    }

    private func loadDashboard() {
        do {
            let database = try SkimDatabase(path: WorkspaceLocator.defaultDatabasePath())
            try database.ensureSchema()
            snapshot = try database.loadDashboard()
            selectedPostID = snapshot.posts.first?.id
            loadError = nil
        } catch {
            loadError = String(describing: error)
        }
    }
}
