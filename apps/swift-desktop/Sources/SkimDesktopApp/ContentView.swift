import SkimDesktopCore
import SwiftUI

struct ContentView: View {
    private let samplePost = DashboardPost(
        id: 1,
        platform: "youtube",
        source: "Demo Channel",
        author: "Skim",
        title: "Swift desktop feed scaffold",
        content: "The data layer is wired in the next goal row.",
        url: URL(string: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        timestamp: nil,
        crawledAt: "fixture"
    )

    var body: some View {
        NavigationSplitView {
            List(selection: .constant(samplePost.id)) {
                postRow(samplePost)
                    .tag(samplePost.id)
            }
            .navigationTitle("Skim")
        } detail: {
            VStack(alignment: .leading, spacing: 16) {
                Text(samplePost.title ?? samplePost.author)
                    .font(.title2.weight(.semibold))
                Text(samplePost.content)
                    .foregroundStyle(.secondary)
                if let url = samplePost.url {
                    Link("Open link", destination: url)
                }
                Spacer()
            }
            .padding(24)
        }
        .frame(minWidth: 980, minHeight: 640)
    }

    private func postRow(_ post: DashboardPost) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(post.platform.uppercased())
                    .font(.caption.weight(.semibold))
                Spacer()
                Text(post.crawledAt)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text(post.title ?? post.author)
                .font(.headline)
                .lineLimit(2)
            Text(post.source ?? post.author)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .padding(.vertical, 6)
    }
}
