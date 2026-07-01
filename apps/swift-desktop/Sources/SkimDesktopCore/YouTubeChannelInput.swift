import Foundation

public enum YouTubeChannelInputError: Error, LocalizedError, Equatable, Sendable {
    case empty
    case unsupported(String)

    public var errorDescription: String? {
        switch self {
        case .empty:
            return "YouTube 채널 URL 또는 핸들을 붙여넣어 주세요."
        case let .unsupported(value):
            return "지원하지 않는 YouTube 채널 입력입니다: \(value)"
        }
    }
}

public struct YouTubeChannelCandidate: Equatable, Sendable {
    public let displayName: String
    public let canonicalID: String
    public let handleOrURL: String
    public let notes: String?

    public var draft: TrackedSourceDraft {
        TrackedSourceDraft(
            platform: "youtube",
            sourceType: "channel",
            displayName: displayName,
            canonicalID: canonicalID,
            handleOrURL: handleOrURL,
            isEnabled: true,
            focusLevel: 0,
            notes: notes
        )
    }
}

public enum YouTubeChannelInput {
    public static func parse(_ rawInput: String) throws -> YouTubeChannelCandidate {
        let input = rawInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !input.isEmpty else {
            throw YouTubeChannelInputError.empty
        }

        if input.hasPrefix("@") {
            return handleCandidate(input)
        }

        guard let url = URL(string: input), let host = url.host()?.lowercased() else {
            throw YouTubeChannelInputError.unsupported(input)
        }

        guard host == "youtube.com" || host == "www.youtube.com" || host == "m.youtube.com" else {
            throw YouTubeChannelInputError.unsupported(input)
        }

        if let channelID = channelID(from: url) {
            return channelCandidate(channelID: channelID, originalURL: input)
        }

        if let handle = handle(from: url) {
            return handleCandidate(handle)
        }

        throw YouTubeChannelInputError.unsupported(input)
    }

    private static func channelID(from url: URL) -> String? {
        if url.path == "/feeds/videos.xml",
           let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
           let channelID = components.queryItems?.first(where: { $0.name == "channel_id" })?.value,
           !channelID.isEmpty
        {
            return channelID
        }

        let parts = url.pathComponents.filter { $0 != "/" }
        guard parts.count >= 2, parts[0].lowercased() == "channel", !parts[1].isEmpty else {
            return nil
        }
        return parts[1]
    }

    private static func handle(from url: URL) -> String? {
        let parts = url.pathComponents.filter { $0 != "/" }
        guard let first = parts.first, first.hasPrefix("@"), first.count > 1 else {
            return nil
        }
        return first
    }

    private static func channelCandidate(channelID: String, originalURL: String) -> YouTubeChannelCandidate {
        YouTubeChannelCandidate(
            displayName: "YouTube \(channelID)",
            canonicalID: channelID,
            handleOrURL: originalURL,
            notes: nil
        )
    }

    private static func handleCandidate(_ rawHandle: String) -> YouTubeChannelCandidate {
        let handle = rawHandle.hasPrefix("@") ? rawHandle : "@\(rawHandle)"
        return YouTubeChannelCandidate(
            displayName: handle,
            canonicalID: handle,
            handleOrURL: "https://www.youtube.com/\(handle)",
            notes: "핸들을 로컬에 저장했습니다. channel_id 해석은 이후 새로고침/가져오기 단계에서 처리합니다."
        )
    }
}
