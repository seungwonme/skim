import Foundation
import Testing

@testable import SkimDesktopCore

// 저장 값은 대부분 UTC(+00:00)다. 시각대 변환 없이 글자만 잘라 보이던 때는
// 15:49 KST 글이 06:49로 보였다 (#80).

private let seoul = TimeZone(identifier: "Asia/Seoul")!

private func post(timestamp: String?, crawledAt: String = "2026-10-11 07:06:04") -> DashboardPost {
    DashboardPost(
        id: 1,
        platform: "threads",
        source: nil,
        author: "author",
        title: nil,
        content: "body",
        url: nil,
        timestamp: timestamp,
        crawledAt: crawledAt
    )
}

@Test(arguments: [
    ("2026-10-11T06:49:31+00:00", "2026-10-11 15:49"),
    ("2026-10-11T15:49:31+09:00", "2026-10-11 15:49"),
    ("2026-10-11T06:49:31Z", "2026-10-11 15:49"),
    ("2026-10-11T06:49:31.250+00:00", "2026-10-11 15:49"),
    ("2026-10-10T20:30:00+00:00", "2026-10-11 05:30")
])
func displayDateConvertsStoredOffsetToLocalTime(stored: String, expected: String) {
    #expect(post(timestamp: stored).displayDate(in: seoul) == expected)
}

@Test(arguments: [nil, ""])
func displayDateFallsBackToCrawledAtAsUTC(timestamp: String?) {
    // crawled_at은 시각대 표기 없는 UTC다.
    #expect(post(timestamp: timestamp).displayDate(in: seoul) == "2026-10-11 16:06")
}

@Test
func displayDateKeepsUnparseableValueReadable() {
    #expect(post(timestamp: "2026-10-11").displayDate(in: seoul) == "2026-10-11")
}
