import AppKit
import Down
import SkimDesktopCore
import SwiftUI
import WebKit

struct ContentView: View {
    @State private var snapshot = DashboardSnapshot.empty
    @State private var selectedPostID: DashboardPost.ID?
    @State private var loadError: String?
    @State private var youtubeInput = ""
    @State private var sourceMessage: Notice?
    @State private var searchText = ""
    @State private var platformFilter: String?
    @State private var readerContentMode = ReaderContentMode.markdown
    @State private var readerMarkdownHeight: CGFloat = 520
    @State private var sortOrder = SortOrder.newest
    @State private var showManager = false
    @State private var managerTab = ManagerTab.sources
    @State private var credentialForm = CredentialForm()
    @State private var credentialNotice: Notice?
    @State private var pendingDeleteCredential: PlatformCredential?
    @State private var isSavingCredential = false
    @FocusState private var focusedCredentialField: CredentialField?

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

    private var sortedPosts: [DashboardPost] {
        switch sortOrder {
        case .newest:
            filteredPosts.sorted { sortDate($0) > sortDate($1) }
        case .likes:
            filteredPosts.sorted { ($0.likes ?? -1) > ($1.likes ?? -1) }
        case .comments:
            filteredPosts.sorted { ($0.comments ?? -1) > ($1.comments ?? -1) }
        }
    }

    private var selectedPost: DashboardPost? {
        sortedPosts.first { $0.id == selectedPostID }
            ?? sortedPosts.first
            ?? snapshot.posts.first
    }

    private var platformCounts: [(name: String, count: Int)] {
        Dictionary(grouping: snapshot.posts, by: \.platform)
            .map { (name: $0.key, count: $0.value.count) }
            .sorted { $0.count > $1.count }
    }

    private func sortDate(_ post: DashboardPost) -> String {
        // timestamp는 ISO("...T..."), crawledAt은 "YYYY-MM-DD HH:MM:SS" — 구분자만 통일하면 사전순 비교 가능
        (post.timestamp?.isEmpty == false ? post.timestamp! : post.crawledAt)
            .replacingOccurrences(of: "T", with: " ")
    }

    var body: some View {
        HSplitView {
            libraryPane
                .frame(minWidth: 220, idealWidth: 248, maxWidth: 300)

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
        .sheet(isPresented: $showManager) {
            managerSheet
                .frame(width: 1040, height: 660)
        }
        .alert("크레덴셜을 삭제할까요?", isPresented: deleteAlertBinding) {
            Button("삭제", role: .destructive) {
                deletePendingCredential()
            }
            Button("취소", role: .cancel) {
                pendingDeleteCredential = nil
            }
        } message: {
            Text(pendingDeleteCredential.map { "\($0.platform) / \($0.loginIdentifier)" } ?? "")
        }
    }

    private var libraryPane: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Skim")
                .font(.system(size: 26, weight: .bold, design: .serif))

            ScrollView {
                LazyVStack(spacing: 3) {
                    platformRow(name: nil, title: "전체", count: snapshot.posts.count)
                    ForEach(platformCounts, id: \.name) { entry in
                        platformRow(name: entry.name, title: entry.name, count: entry.count)
                    }
                }
            }
            .scrollIndicators(.hidden)

            Spacer(minLength: 8)

            VStack(alignment: .leading, spacing: 10) {
                Button {
                    showManager = true
                } label: {
                    Label("소스/크레덴셜 관리", systemImage: "gearshape")
                }
                .buttonStyle(.borderless)
                Button {
                    loadDashboard()
                } label: {
                    Label("새로고침", systemImage: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                Text(snapshot.databasePath.isEmpty ? "data/skim.db" : snapshot.databasePath)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
        }
        .padding(18)
        .background(Design.sidebarBackground)
    }

    private func platformRow(name: String?, title: String, count: Int) -> some View {
        let isSelected = platformFilter == name
        return Button {
            platformFilter = name
        } label: {
            HStack(spacing: 9) {
                Circle()
                    .fill(name.map(platformColor) ?? Design.primaryText.opacity(0.55))
                    .frame(width: 7, height: 7)
                Text(title)
                    .font(.system(size: 13, weight: isSelected ? .semibold : .regular))
                    .foregroundStyle(Design.primaryText)
                    .lineLimit(1)
                Spacer()
                Text(count.formatted())
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 10)
            .frame(height: 30)
            .background(isSelected ? Design.selectedBackground : Color.clear, in: RoundedRectangle(cornerRadius: 6))
            .contentShape(RoundedRectangle(cornerRadius: 6))
        }
        .buttonStyle(.plain)
    }

    private var feedPane: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(platformFilter ?? "전체")
                        .font(.system(size: 22, weight: .semibold, design: .serif))
                    Text("\(filteredPosts.count.formatted())개")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Picker("정렬", selection: $sortOrder) {
                    ForEach(SortOrder.allCases, id: \.self) { order in
                        Text(order.rawValue).tag(order)
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .fixedSize()
            }

            TextField("제목, 소스, 요약 검색", text: $searchText)
                .textFieldStyle(.plain)
                .padding(.horizontal, 12)
                .frame(height: 38)
                .background(Design.inputBackground, in: RoundedRectangle(cornerRadius: 7))

            if let loadError {
                ContentUnavailableView("데이터를 불러오지 못했습니다", systemImage: "exclamationmark.triangle", description: Text(loadError))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if filteredPosts.isEmpty {
                ContentUnavailableView("결과 없음", systemImage: "tray", description: Text("검색어나 플랫폼 필터를 조정하세요."))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 10) {
                        ForEach(sortedPosts) { post in
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

    private var readerPane: some View {
        Group {
            if let post = selectedPost {
                VStack(spacing: 0) {
                    readerTopBar(post)
                    Divider()

                    ScrollView {
                        VStack(alignment: .leading, spacing: 18) {
                            infoStrip(post)

                            if let url = post.url {
                                PreviewPane(preview: ContentPreview.classify(url))
                            }

                            VStack(alignment: .leading, spacing: 10) {
                                sectionLabel(readerContentMode == .markdown ? "MD 렌더링" : "코드")
                                readerBody(post)
                            }
                        }
                        .padding(30)
                        .frame(maxWidth: 820, alignment: .leading)
                    }
                    .scrollIndicators(.automatic)
                }
                .background(Design.readerBackground)
            } else {
                ContentUnavailableView("포스트를 선택하세요", systemImage: "sidebar.right")
                    .background(Design.readerBackground)
            }
        }
    }

    private func readerTopBar(_ post: DashboardPost) -> some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    platformBadge(post.platform)
                    Text(post.source ?? post.author)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Text(post.displayTitle)
                    .font(.system(size: 26, weight: .semibold, design: .serif))
                    .lineSpacing(2)
                    .lineLimit(2)
                    .textSelection(.enabled)
            }
            Spacer(minLength: 16)
            HStack(spacing: 10) {
                readerModeToggle
                if let url = post.url {
                    Link(destination: url) {
                        Label("열기", systemImage: "arrow.up.right")
                    }
                    .buttonStyle(.bordered)
                }
            }
        }
        .padding(.horizontal, 30)
        .padding(.vertical, 18)
        .background(Design.readerBackground)
    }

    private var managerSheet: some View {
        VStack(spacing: 0) {
            HStack {
                Picker("", selection: $managerTab) {
                    Text("소스").tag(ManagerTab.sources)
                    Text("크레덴셜").tag(ManagerTab.credentials)
                }
                .pickerStyle(.segmented)
                .frame(width: 240)
                Spacer()
                Button("닫기") {
                    showManager = false
                }
                .keyboardShortcut(.cancelAction)
            }
            .padding(16)

            Divider()

            if managerTab == .sources {
                sourcesManagerPane
            } else {
                HSplitView {
                    credentialsPane
                        .frame(minWidth: 400, idealWidth: 450)
                    credentialEditorPane
                        .frame(minWidth: 480)
                }
            }
        }
        .background(Design.windowBackground)
    }

    private var sourcesManagerPane: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 10) {
                sectionLabel("YouTube 구독 추가")
                HStack(spacing: 8) {
                    TextField("youtube.com/@채널", text: $youtubeInput)
                        .textFieldStyle(.plain)
                        .padding(.horizontal, 10)
                        .frame(height: 34)
                        .frame(maxWidth: 420)
                        .background(Design.inputBackground, in: RoundedRectangle(cornerRadius: 7))
                    Button {
                        addYouTubeSource()
                    } label: {
                        Image(systemName: "plus")
                            .frame(width: 30, height: 30)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(youtubeInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                if let sourceMessage {
                    Text(sourceMessage.text)
                        .font(.caption)
                        .foregroundStyle(sourceMessage.isError ? Color.red : Design.green)
                        .lineLimit(2)
                }
            }

            VStack(alignment: .leading, spacing: 10) {
                sectionLabel("추적 중인 소스 \(snapshot.sources.count.formatted())개")
                if snapshot.sources.isEmpty {
                    emptyLine("추적 중인 소스 없음")
                } else {
                    ScrollView {
                        LazyVStack(spacing: 6) {
                            ForEach(snapshot.sources) { source in
                                sourceRow(source)
                            }
                        }
                    }
                    .scrollIndicators(.automatic)
                }
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Design.feedBackground)
    }

    private var credentialsPane: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("크레덴셜")
                        .font(.system(size: 22, weight: .semibold, design: .serif))
                    Text("저장된 계정 \(snapshot.credentials.count.formatted())개")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    credentialForm = CredentialForm()
                    credentialNotice = nil
                    isSavingCredential = false
                    DispatchQueue.main.async {
                        focusedCredentialField = .accountLabel
                    }
                } label: {
                    Label("새로 만들기", systemImage: "plus")
                }
                .buttonStyle(.borderedProminent)
            }

            if snapshot.credentials.isEmpty {
                ContentUnavailableView("저장된 크레덴셜 없음", systemImage: "key", description: Text("계정을 추가하면 로그인 메타데이터는 DB에, 비밀번호는 macOS 키체인에 저장됩니다."))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 10) {
                        ForEach(snapshot.credentials) { credential in
                            credentialRow(credential)
                        }
                    }
                    .padding(.bottom, 18)
                }
            }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 20)
        .background(Design.feedBackground)
    }

    private var credentialEditorPane: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(credentialForm.id == nil ? "크레덴셜 추가" : "크레덴셜 수정")
                        .font(.system(size: 30, weight: .semibold, design: .serif))
                    Text("메타데이터는 SQLite에 저장하고, 비밀번호는 macOS 키체인에 저장합니다. 서비스 이름은 skim.desktop.<플랫폼> 형식입니다.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                VStack(alignment: .leading, spacing: 14) {
                    Picker("플랫폼", selection: $credentialForm.platform) {
                        Text("Threads").tag("threads")
                        Text("X").tag("x")
                        Text("LinkedIn").tag("linkedin")
                        Text("Reddit").tag("reddit")
                    }
                    .pickerStyle(.segmented)

                    credentialTextField(.accountLabel, title: "계정 라벨", text: $credentialForm.accountLabel, prompt: "개인 / 업무")
                    credentialTextField(.loginIdentifier, title: "로그인 식별자", text: $credentialForm.loginIdentifier, prompt: "이메일 또는 사용자명")

                    VStack(alignment: .leading, spacing: 6) {
                        SecureField(credentialForm.passwordRequired ? "비밀번호 필수" : "새 비밀번호 선택 입력", text: $credentialForm.password)
                            .textFieldStyle(.plain)
                            .focused($focusedCredentialField, equals: .password)
                            .padding(.horizontal, 12)
                            .frame(height: 38)
                            .background(Design.inputBackground, in: RoundedRectangle(cornerRadius: 7))
                            .overlay(inputBorder(isFocused: focusedCredentialField == .password).allowsHitTesting(false))
                        Text(credentialForm.passwordRequired ? "새 크레덴셜이거나 플랫폼/로그인 식별자를 바꾸는 경우 필요합니다." : "비워두면 기존 키체인 비밀번호를 유지합니다.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    if let credentialNotice, credentialNotice.isError || !credentialForm.hasChanges {
                        Text(credentialNotice.text)
                            .font(.callout)
                            .foregroundStyle(credentialNotice.isError ? Color.red : Design.green)
                    }

                    credentialSaveStatus

                    HStack {
                        Button {
                            saveCredentialForm()
                        } label: {
                            Label(credentialSaveButtonTitle, systemImage: credentialSaveButtonIcon)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(canSaveCredential ? Design.green : Color.gray)
                        .disabled(!canSaveCredential)

                        Button {
                            credentialForm = credentialForm.resettingChanges()
                            credentialNotice = nil
                        } label: {
                            Label(credentialForm.id == nil ? "초기화" : "되돌리기", systemImage: "arrow.uturn.backward")
                        }
                        .buttonStyle(.bordered)
                        .disabled(isSavingCredential || !credentialForm.hasChanges)
                    }
                }
                .padding(16)
                .background(Design.panelBackground, in: RoundedRectangle(cornerRadius: 8))

                VStack(alignment: .leading, spacing: 8) {
                    sectionLabel("키체인 세부정보")
                    Text("서비스: \(KeychainStore.secretService(platform: credentialForm.platform))")
                    Text("계정: \(credentialForm.loginIdentifier.isEmpty ? "-" : credentialForm.loginIdentifier)")
                    Text("세션 경로: data/sessions/\(credentialForm.platform)_session.json")
                }
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
                .padding(16)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Design.panelBackground.opacity(0.72), in: RoundedRectangle(cornerRadius: 8))

                Spacer(minLength: 20)
            }
            .padding(30)
            .frame(maxWidth: 760, alignment: .leading)
        }
        .background(Design.readerBackground)
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

                    Text(cardPreview(post))
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

    private func credentialRow(_ credential: PlatformCredential) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                platformBadge(credential.platform)
                Text(credential.accountLabel)
                    .font(.headline)
                    .foregroundStyle(Design.primaryText)
                    .lineLimit(1)
                Spacer()
                Text(sessionStatusLabel(credential.sessionStatus))
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(isSessionHealthy(credential.sessionStatus) ? Design.green : .secondary)
            }
            Text(credential.loginIdentifier)
                .font(.callout.monospaced())
                .foregroundStyle(.secondary)
                .lineLimit(1)
            HStack {
                Text(credential.sessionPath ?? "세션 경로 없음")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                Spacer()
                Button {
                    selectCredentialForEditing(credential)
                } label: {
                    Label("수정", systemImage: "pencil")
                }
                .buttonStyle(.borderless)
                .foregroundStyle(Design.green)
                Button(role: .destructive) {
                    pendingDeleteCredential = credential
                } label: {
                    Label("삭제", systemImage: "trash")
                }
                .buttonStyle(.borderless)
                .foregroundStyle(Color.red)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(credentialForm.id == credential.id ? Design.selectedBackground : Design.cardBackground, in: RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(credentialForm.id == credential.id ? Design.green.opacity(0.5) : Design.hairline, lineWidth: 1)
        )
        .contentShape(RoundedRectangle(cornerRadius: 8))
        .onTapGesture {
            selectCredentialForEditing(credential)
        }
    }

    private func credentialTextField(_ field: CredentialField, title: String, text: Binding<String>, prompt: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            sectionLabel(title)
            TextField(prompt, text: text)
                .textFieldStyle(.plain)
                .focused($focusedCredentialField, equals: field)
                .padding(.horizontal, 12)
                .frame(height: 38)
                .background(Design.inputBackground, in: RoundedRectangle(cornerRadius: 7))
                .overlay(inputBorder(isFocused: focusedCredentialField == field).allowsHitTesting(false))
                .onTapGesture {
                    focusedCredentialField = field
                }
        }
    }

    private var credentialSaveStatus: some View {
        HStack(spacing: 8) {
            if isSavingCredential {
                ProgressView()
                    .controlSize(.small)
                Text("키체인과 SQLite에 저장하는 중")
            } else if credentialForm.hasChanges {
                Image(systemName: "circle.fill")
                    .font(.system(size: 7))
                    .foregroundStyle(Design.amber)
                Text("저장되지 않은 변경사항")
            } else if credentialForm.id != nil {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(Design.green)
                Text("현재 내용이 저장되어 있음")
            } else {
                Image(systemName: "circle.dashed")
                    .foregroundStyle(.secondary)
                Text("필수 항목을 입력하면 저장할 수 있음")
            }
        }
        .font(.caption.weight(.medium))
        .foregroundStyle(.secondary)
        .padding(.horizontal, 12)
        .frame(height: 34)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Design.inputBackground.opacity(0.7), in: RoundedRectangle(cornerRadius: 7))
    }

    private var canSaveCredential: Bool {
        credentialForm.isSaveable && credentialForm.hasChanges && !isSavingCredential
    }

    private var credentialSaveButtonTitle: String {
        if isSavingCredential {
            return "저장 중"
        }
        if !credentialForm.hasChanges {
            return credentialForm.id == nil ? "입력 필요" : "변경 없음"
        }
        return credentialForm.id == nil ? "크레덴셜 저장" : "변경사항 저장"
    }

    private var credentialSaveButtonIcon: String {
        if isSavingCredential {
            return "arrow.triangle.2.circlepath"
        }
        return credentialForm.hasChanges ? "checkmark" : "checkmark.circle"
    }

    private var readerModeToggle: some View {
        HStack(spacing: 2) {
            readerModeButton(.markdown, title: "MD")
            readerModeButton(.raw, title: "코드")
        }
        .padding(3)
        .background(Design.panelBackground.opacity(0.95), in: Capsule())
        .overlay(Capsule().stroke(Design.hairline, lineWidth: 1))
    }

    private func readerModeButton(_ mode: ReaderContentMode, title: String) -> some View {
        Button {
            readerContentMode = mode
        } label: {
            Text(title)
                .font(.caption.weight(.bold))
                .foregroundStyle(readerContentMode == mode ? Color.white : Design.primaryText)
                .padding(.horizontal, 10)
                .frame(height: 26)
                .background(readerContentMode == mode ? Design.green : Color.clear, in: Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
    }

    @ViewBuilder
    private func readerBody(_ post: DashboardPost) -> some View {
        let markdown = readerMarkdown(post)
        if readerContentMode == .markdown {
            ReaderMarkdownWebView(markdown: markdown, contentHeight: $readerMarkdownHeight)
                .frame(height: max(readerMarkdownHeight, 260))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .allowsHitTesting(false)
        } else {
            Text(markdown)
                .font(.system(.body, design: .monospaced))
                .foregroundStyle(.secondary)
                .lineSpacing(4)
                .textSelection(.enabled)
        }
    }

    private func readerMarkdown(_ post: DashboardPost) -> String {
        var markdown: String
        if let body = post.contentMarkdown?.trimmingCharacters(in: .whitespacesAndNewlines),
           !body.isEmpty
        {
            markdown = body
        } else {
            markdown = post.summary ?? post.content
        }

        // SNS 첨부/og:image 중 본문에 아직 없는 것만 뒤에 붙인다 (인라인 이미지와 중복 방지)
        let attached = post.imageURLs.filter { !markdown.contains($0) }
        if !attached.isEmpty {
            markdown += "\n\n" + attached.map { "![](\($0))" }.joined(separator: "\n\n")
        }
        return markdown
    }

    private func inputBorder(isFocused: Bool) -> some View {
        RoundedRectangle(cornerRadius: 7)
            .stroke(isFocused ? Design.green.opacity(0.85) : Design.hairline, lineWidth: isFocused ? 1.5 : 1)
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
            infoItem("작성자", post.author)
            if let source = post.source {
                infoItem("소스", source)
            }
            infoItem("날짜", displayDate(post))
            if let likes = post.likes {
                infoItem("좋아요", likes.formatted())
            }
            if let comments = post.comments {
                infoItem("댓글", comments.formatted())
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

    private func cardPreview(_ post: DashboardPost) -> String {
        // hnrss summary는 "Article URL: ..." 메타 텍스트라 미리보기로 부적합 — 본문으로 대체
        if let summary = post.summary, !summary.isEmpty, !summary.hasPrefix("Article URL:") {
            return summary
        }
        let body = post.contentMarkdown?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return body.isEmpty ? post.content : String(body.prefix(300))
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
        let databasePath = WorkspaceLocator.defaultDatabasePath()
        Task { @MainActor in
            do {
                // 본문 포함 180건 로드를 메인 스레드에서 돌리면 실행/새로고침마다 UI가 멈춘다.
                let loaded = try await Task.detached(priority: .userInitiated) {
                    let database = try SkimDatabase(path: databasePath)
                    try database.ensureSchema()
                    return try database.loadDashboard(limit: 180)
                }.value
                snapshot = loaded
                if let selectedPostID, loaded.posts.contains(where: { $0.id == selectedPostID }) {
                    self.selectedPostID = selectedPostID
                } else {
                    selectedPostID = loaded.posts.first?.id
                }
                loadError = nil
            } catch {
                loadError = localizedError(error)
            }
        }
    }

    private func saveCredentialForm() {
        guard credentialForm.isSaveable, credentialForm.hasChanges, !isSavingCredential else {
            return
        }

        isSavingCredential = true
        credentialNotice = nil
        let draft = credentialForm.draft
        let databasePath = WorkspaceLocator.defaultDatabasePath()

        Task { @MainActor in
            do {
                let saved = try await Task.detached(priority: .userInitiated) {
                    let database = try SkimDatabase(path: databasePath)
                    try database.ensureSchema()
                    return try database.saveCredential(draft)
                }.value
                credentialForm = CredentialForm(credential: saved)
                updateSnapshot(with: saved)
                credentialNotice = Notice(text: "\(saved.accountLabel) 저장 완료", isError: false)
                loadDashboard()
            } catch {
                credentialNotice = Notice(text: localizedError(error), isError: true)
            }
            isSavingCredential = false
        }
    }

    private func selectCredentialForEditing(_ credential: PlatformCredential) {
        credentialForm = CredentialForm(credential: credential)
        credentialNotice = nil
        isSavingCredential = false
        DispatchQueue.main.async {
            focusedCredentialField = .accountLabel
        }
    }

    private func updateSnapshot(with credential: PlatformCredential) {
        var credentials = snapshot.credentials.filter { $0.id != credential.id }
        credentials.append(credential)
        credentials.sort {
            if $0.platform != $1.platform {
                return $0.platform < $1.platform
            }
            return $0.accountLabel.localizedCaseInsensitiveCompare($1.accountLabel) == .orderedAscending
        }
        snapshot = DashboardSnapshot(
            summary: DashboardSummary(
                postsCount: snapshot.summary.postsCount,
                sourcesCount: snapshot.summary.sourcesCount,
                credentialsCount: credentials.count
            ),
            posts: snapshot.posts,
            sources: snapshot.sources,
            credentials: credentials,
            databasePath: snapshot.databasePath
        )
    }

    private var deleteAlertBinding: Binding<Bool> {
        Binding(
            get: { pendingDeleteCredential != nil },
            set: { isPresented in
                if !isPresented {
                    pendingDeleteCredential = nil
                }
            }
        )
    }

    private func deletePendingCredential() {
        guard let credential = pendingDeleteCredential else {
            return
        }

        do {
            let database = try SkimDatabase(path: WorkspaceLocator.defaultDatabasePath())
            try database.ensureSchema()
            try database.deleteCredential(id: credential.id)
            if credentialForm.id == credential.id {
                credentialForm = CredentialForm()
            }
            credentialNotice = Notice(text: "\(credential.platform) / \(credential.loginIdentifier) 삭제됨", isError: false)
            pendingDeleteCredential = nil
            loadDashboard()
        } catch {
            credentialNotice = Notice(text: localizedError(error), isError: true)
            pendingDeleteCredential = nil
        }
    }

    private func addYouTubeSource() {
        do {
            let candidate = try YouTubeChannelInput.parse(youtubeInput)
            let database = try SkimDatabase(path: WorkspaceLocator.defaultDatabasePath())
            try database.ensureSchema()
            let source = try database.upsertTrackedSource(candidate.draft)
            youtubeInput = ""
            sourceMessage = Notice(text: "\(source.displayName) 추가됨", isError: false)
            loadDashboard()
        } catch {
            sourceMessage = Notice(text: localizedError(error), isError: true)
        }
    }

    private func sessionStatusLabel(_ status: String) -> String {
        switch status {
        case "healthy": "정상"
        case "missing": "없음"
        default: status
        }
    }

    private func isSessionHealthy(_ status: String) -> Bool {
        status == "healthy"
    }

    private func localizedError(_ error: Error) -> String {
        if let localized = error as? LocalizedError, let description = localized.errorDescription {
            return description
        }
        return error.localizedDescription
    }
}

private struct Notice: Equatable {
    let text: String
    let isError: Bool
}

private enum SortOrder: String, CaseIterable {
    case newest = "최신순"
    case likes = "점수순"
    case comments = "댓글순"
}

private enum ManagerTab: Equatable {
    case sources
    case credentials
}

private enum ReaderContentMode: Equatable {
    case markdown
    case raw
}

private struct ReaderMarkdownWebView: NSViewRepresentable {
    let markdown: String
    @Binding var contentHeight: CGFloat

    func makeCoordinator() -> Coordinator {
        Coordinator(contentHeight: $contentHeight)
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        // 크롤링된 외부 콘텐츠 뷰어라 페이지 JS가 필요 없다. 실행 표면을 닫는다.
        // (네이티브 evaluateJavaScript 기반 높이 측정에는 영향 없음)
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = false
        webView.setValue(false, forKey: "drawsBackground")
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        let html = MarkdownHTMLRenderer.document(markdown)
        guard context.coordinator.html != html else {
            return
        }
        context.coordinator.html = html
        contentHeight = 260
        webView.loadHTMLString(html, baseURL: nil)
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var html = ""
        private let contentHeight: Binding<CGFloat>

        init(contentHeight: Binding<CGFloat>) {
            self.contentHeight = contentHeight
        }

        func webView(_ webView: WKWebView, didFinish _: WKNavigation!) {
            webView.evaluateJavaScript("Math.ceil(Math.max(document.body.scrollHeight, document.documentElement.scrollHeight))") { [weak self] result, _ in
                guard let self else {
                    return
                }
                let height = (result as? NSNumber).map { CGFloat($0.doubleValue) } ?? 260
                DispatchQueue.main.async {
                    self.contentHeight.wrappedValue = max(height + 8, 260)
                }
            }
        }
    }
}

private enum MarkdownHTMLRenderer {
    static func document(_ markdown: String) -> String {
        let normalizedMarkdown = promoteImageLinks(markdown)
        let body = (try? Down(markdownString: normalizedMarkdown).toHTML()) ?? fallbackHTML(markdown)
        return """
        <!doctype html>
        <html>
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
              :root { color-scheme: light dark; --text: #202124; --border: rgba(0,0,0,.12); --soft: rgba(0,0,0,.055); --code: #f5f5f5; --accent: #1769e0; }
              @media (prefers-color-scheme: dark) { :root { --text: #d7d7d7; --border: rgba(255,255,255,.12); --soft: rgba(255,255,255,.06); --code: #242424; --accent: #62a8ff; } }
              html, body { margin: 0; padding: 0; background: transparent; color: var(--text); font: 15px/1.68 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Apple SD Gothic Neo", sans-serif; overflow: hidden; }
              body { padding-bottom: 22px; }
              h1, h2, h3 { margin: 1.2em 0 .45em; line-height: 1.24; font-weight: 750; }
              h1 { font-size: 28px; border-bottom: 1px solid var(--border); padding-bottom: .28em; }
              h2 { font-size: 23px; border-bottom: 1px solid var(--border); padding-bottom: .22em; }
              h3 { font-size: 19px; }
              p { margin: .75em 0; }
              a { color: var(--accent); text-decoration: none; }
              ul, ol { margin: .72em 0 .72em 1.55em; padding: 0; }
              li { margin: .32em 0; }
              blockquote { margin: 1em 0; padding-left: 1em; border-left: 3px solid var(--border); opacity: .78; }
              code { padding: .12em .35em; border-radius: 5px; background: var(--code); font: .92em "SF Mono", ui-monospace, Menlo, monospace; }
              pre { margin: 1em 0; padding: 14px 16px; overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; background: var(--code); }
              pre code { padding: 0; background: transparent; white-space: pre; }
              table { width: 100%; margin: 1em 0; border-collapse: collapse; font-size: 14px; }
              th, td { padding: 8px 10px; border: 1px solid var(--border); vertical-align: top; }
              th { background: var(--soft); font-weight: 700; }
              img { display: block; max-width: 100%; height: auto; margin: 1em 0; border-radius: 8px; }
            </style>
          </head>
          <body>\(body)</body>
        </html>
        """
    }

    private static func promoteImageLinks(_ markdown: String) -> String {
        guard let expression = try? NSRegularExpression(pattern: #"(?<!!)\[([^\]]+)\]\(([^)]+)\)"#) else {
            return markdown
        }

        var result = markdown
        let source = markdown as NSString
        let matches = expression.matches(
            in: markdown,
            range: NSRange(markdown.startIndex..<markdown.endIndex, in: markdown)
        )
        for match in matches.reversed() {
            guard match.numberOfRanges == 3,
                  let range = Range(match.range, in: result)
            else {
                continue
            }

            let label = source.substring(with: match.range(at: 1))
            let url = source.substring(with: match.range(at: 2))
            guard isImageReference(label: label, url: url) else {
                continue
            }

            result.replaceSubrange(range, with: "![\(label)](\(url))")
        }
        return result
    }

    private static func isImageReference(label: String, url: String) -> Bool {
        let value = "\(label) \(url)"
            .replacingOccurrences(of: "&amp;", with: "&")
            .lowercased()
        return [".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".svg", ".fpng"].contains {
            value.contains($0)
        }
    }

    private static func fallbackHTML(_ markdown: String) -> String {
        "<pre><code>\(escape(markdown))</code></pre>"
    }

    private static func escape(_ text: String) -> String {
        text
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
    }

}

private enum CredentialField: Hashable {
    case accountLabel
    case loginIdentifier
    case password
}

private struct CredentialForm: Equatable {
    var id: Int64?
    var platform = "threads"
    var accountLabel = ""
    var loginIdentifier = ""
    var password = ""
    var originalPlatform: String?
    var originalAccountLabel: String?
    var originalLoginIdentifier: String?

    init() {}

    init(credential: PlatformCredential) {
        id = credential.id
        platform = credential.platform
        accountLabel = credential.accountLabel
        loginIdentifier = credential.loginIdentifier
        originalPlatform = credential.platform
        originalAccountLabel = credential.accountLabel
        originalLoginIdentifier = credential.loginIdentifier
    }

    var passwordRequired: Bool {
        id == nil || platform != originalPlatform || loginIdentifier != originalLoginIdentifier
    }

    var isSaveable: Bool {
        !platform.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !accountLabel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !loginIdentifier.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            (!passwordRequired || !password.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    var hasChanges: Bool {
        if id == nil {
            return isSaveable
        }
        return platform != originalPlatform ||
            accountLabel != originalAccountLabel ||
            loginIdentifier != originalLoginIdentifier ||
            !password.isEmpty
    }

    var draft: PlatformCredentialDraft {
        PlatformCredentialDraft(
            id: id,
            platform: platform,
            accountLabel: accountLabel,
            loginIdentifier: loginIdentifier,
            password: password.isEmpty ? nil : password
        )
    }

    func resettingChanges() -> CredentialForm {
        guard id != nil else {
            return CredentialForm()
        }
        var form = self
        form.platform = originalPlatform ?? platform
        form.accountLabel = originalAccountLabel ?? accountLabel
        form.loginIdentifier = originalLoginIdentifier ?? loginIdentifier
        form.password = ""
        return form
    }
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
