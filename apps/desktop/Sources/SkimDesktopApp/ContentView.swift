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
    @State private var columnVisibility = NavigationSplitViewVisibility.all
    @State private var sourceFilter: String?
    @State private var channelPosts: [DashboardPost] = []
    @State private var loadedYears: [String: Int] = [:]
    @State private var channelBusyMessage: String?
    @State private var exhaustedChannels: Set<String> = []
    @State private var transcribingPostID: DashboardPost.ID?
    @State private var credentialForm = CredentialForm()
    @State private var credentialNotice: Notice?
    @State private var pendingDeleteCredential: PlatformCredential?
    @State private var isSavingCredential = false
    @State private var isLoadingMore = false
    @State private var hasMorePosts = true
    private let pageSize = 200
    @FocusState private var focusedCredentialField: CredentialField?

    private var filteredPosts: [DashboardPost] {
        (sourceFilter != nil ? channelPosts : snapshot.posts).filter { post in
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
        // 일반 피드는 DB가 정렬 기준대로 전체에서 정렬해 로드하므로 순서를 그대로 둔다.
        // 채널 모드(sourceFilter)는 로드된 전체에서 클라이언트 정렬한다.
        guard sourceFilter != nil else { return filteredPosts }
        switch sortOrder {
        case .newest:
            return filteredPosts.sorted { sortDate($0) > sortDate($1) }
        case .likes:
            return filteredPosts.sorted { ($0.likes ?? -1) > ($1.likes ?? -1) }
        case .comments:
            return filteredPosts.sorted { ($0.comments ?? -1) > ($1.comments ?? -1) }
        }
    }

    private var dbSort: SkimDatabase.PostSort {
        switch sortOrder {
        case .newest: .newest
        case .likes: .likes
        case .comments: .comments
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
        NavigationSplitView(columnVisibility: $columnVisibility) {
            sidebarList
                .navigationSplitViewColumnWidth(min: 200, ideal: 232, max: 300)
        } content: {
            feedList
                .navigationSplitViewColumnWidth(min: 340, ideal: 420)
        } detail: {
            readerPane
                .navigationSplitViewColumnWidth(min: 500, ideal: 700)
        }
        .frame(minWidth: 1180, minHeight: 720)
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

    private var sidebarSelection: Binding<SidebarItem?> {
        Binding(
            get: {
                if let sourceFilter {
                    return .channel(String(sourceFilter.dropFirst("youtube/".count)))
                }
                if let platformFilter {
                    return .platform(platformFilter)
                }
                return .all
            },
            set: { newValue in
                switch newValue {
                case .all, nil:
                    platformFilter = nil
                    sourceFilter = nil
                case let .platform(name):
                    platformFilter = name
                    sourceFilter = nil
                case let .channel(name):
                    selectChannel(name)
                }
            }
        )
    }

    private var sidebarList: some View {
        List(selection: sidebarSelection) {
            Section("피드") {
                Label("전체", systemImage: "tray.full")
                    .badge(snapshot.posts.count)
                    .tag(SidebarItem.all)
                ForEach(platformCounts, id: \.name) { entry in
                    Label {
                        Text(entry.name)
                    } icon: {
                        Image(systemName: "circle.fill")
                            .font(.system(size: 8))
                            .foregroundStyle(platformColor(entry.name))
                    }
                    .badge(entry.count)
                    .tag(SidebarItem.platform(entry.name))
                }
            }
            if platformFilter == "youtube" {
                Section("YouTube 채널") {
                    ForEach(snapshot.sources.filter { $0.platform == "youtube" }.sorted { $0.displayName < $1.displayName }) { source in
                        Label(source.displayName, systemImage: "play.rectangle")
                            .tag(SidebarItem.channel(source.displayName))
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("Skim")
    }

    private var feedTitle: String {
        sourceFilter.map { String($0.dropFirst("youtube/".count)) } ?? platformFilter ?? "전체"
    }

    private var feedList: some View {
        Group {
            if let loadError {
                ContentUnavailableView("데이터를 불러오지 못했습니다", systemImage: "exclamationmark.triangle", description: Text(loadError))
            } else if sortedPosts.isEmpty {
                ContentUnavailableView("결과 없음", systemImage: "tray", description: Text("검색어나 필터를 조정하세요."))
            } else {
                List(selection: $selectedPostID) {
                    ForEach(sortedPosts) { post in
                        feedRow(post)
                            .tag(post.id)
                            .onAppear {
                                // 일반 피드에서 목록 끝 근처(뒤에서 8번째 이내)면 다음 페이지 로드
                                if sourceFilter == nil,
                                   let idx = sortedPosts.firstIndex(where: { $0.id == post.id }),
                                   idx >= sortedPosts.count - 8 {
                                    loadMorePosts()
                                }
                            }
                    }
                    if sourceFilter != nil {
                        loadMoreButton
                            .listRowSeparator(.hidden)
                    } else if isLoadingMore {
                        HStack {
                            Spacer()
                            ProgressView()
                                .controlSize(.small)
                            Text("더 불러오는 중…")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                        }
                        .padding(.vertical, 6)
                        .listRowSeparator(.hidden)
                    }
                }
                .listStyle(.inset)
            }
        }
        .searchable(text: $searchText, placement: .toolbar, prompt: "제목, 소스, 요약 검색")
        .navigationTitle(feedTitle)
        .navigationSubtitle(channelBusyMessage ?? "\(filteredPosts.count.formatted())개")
        .onChange(of: sortOrder) { _, _ in
            if sourceFilter == nil { loadDashboard() }
        }
        .toolbar {
            ToolbarItemGroup {
                Menu {
                    Picker("정렬", selection: $sortOrder) {
                        ForEach(SortOrder.allCases, id: \.self) { order in
                            Text(order.rawValue).tag(order)
                        }
                    }
                    .pickerStyle(.inline)
                } label: {
                    Label("정렬", systemImage: "arrow.up.arrow.down")
                }
                Button {
                    loadDashboard()
                } label: {
                    Label("새로고침", systemImage: "arrow.clockwise")
                }
                Button {
                    showManager = true
                } label: {
                    Label("소스/크레덴셜 관리", systemImage: "gearshape")
                }
            }
        }
    }

    private func feedRow(_ post: DashboardPost) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Circle()
                    .fill(platformColor(post.platform))
                    .frame(width: 7, height: 7)
                Text(post.source ?? post.platform)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                Spacer()
                Text(displayDate(post))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            Text(post.displayTitle)
                .font(.system(size: 13, weight: .semibold))
                .lineLimit(2)
            Text(cardPreview(post))
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(2)
            transcribeIndicator(post)
            if post.likes != nil || post.comments != nil {
                HStack(spacing: 10) {
                    if let likes = post.likes {
                        Label(likes.formatted(), systemImage: "hand.thumbsup")
                    }
                    if let comments = post.comments {
                        Label(comments.formatted(), systemImage: "text.bubble")
                    }
                }
                .font(.caption2)
                .foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 4)
        .opacity(isTranscribeBlocked(post) ? 0.5 : 1)
    }

    /// 전사 중인 영상은 하나뿐이라, 리스트에서 진행/대기 상태를 시각화한다
    @ViewBuilder
    private func transcribeIndicator(_ post: DashboardPost) -> some View {
        if transcribingPostID == post.id {
            HStack(spacing: 5) {
                ProgressView()
                    .controlSize(.small)
                Text("전사 중")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(Design.red)
            }
        } else if isTranscribeBlocked(post) {
            Label("다른 영상 전사 중 - 대기", systemImage: "hourglass")
                .font(.caption2.weight(.medium))
                .foregroundStyle(.tertiary)
        }
    }

    /// 자막 없는 YouTube 영상인데 다른 영상이 전사 중이라 지금은 시작할 수 없음
    private func isTranscribeBlocked(_ post: DashboardPost) -> Bool {
        transcribingPostID != nil
            && transcribingPostID != post.id
            && post.platform == "youtube"
            && (post.contentMarkdown ?? "").isEmpty
    }

    private var loadMoreButton: some View {
        Button {
            loadMoreYears()
        } label: {
            Label(
                channelBusyMessage ?? "이전 1년 더 불러오기 (현재 \(currentChannelYears)년치)",
                systemImage: "clock.arrow.circlepath"
            )
            .frame(maxWidth: .infinity)
            .padding(.vertical, 10)
        }
        .buttonStyle(.bordered)
        .disabled(channelBusyMessage != nil)
    }

    private var currentChannelYears: Int {
        sourceFilter.flatMap { loadedYears[$0] } ?? 1
    }

    /// 채널 뷰이고 아직 마지막 영상까지 소진되지 않은 경우에만 더 불러오기 노출
    private var canLoadMoreChannel: Bool {
        guard let sourceFilter else {
            return false
        }
        return !exhaustedChannels.contains(sourceFilter)
    }

    private func transcribeButtonTitle(_ post: DashboardPost) -> String {
        if transcribingPostID == post.id {
            return "전사 중"
        }
        if transcribingPostID != nil {
            return "다른 영상 전사 중"
        }
        return "자막 전사"
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
                    .font(.title2.weight(.semibold))
                    .lineSpacing(2)
                    .lineLimit(2)
                    .textSelection(.enabled)
            }
            Spacer(minLength: 16)
            HStack(spacing: 10) {
                if post.platform == "youtube", (post.contentMarkdown ?? "").isEmpty {
                    Button {
                        transcribe(post)
                    } label: {
                        Label(
                            transcribeButtonTitle(post),
                            systemImage: transcribingPostID == post.id ? "hourglass" : "captions.bubble"
                        )
                    }
                    .buttonStyle(.bordered)
                    .disabled(transcribingPostID != nil)
                }
                readerModeToggle
                if let url = post.url {
                    Button {
                        NSWorkspace.shared.open(url)
                    } label: {
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
                        .font(.title3.weight(.semibold))
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
                        .font(.title.weight(.semibold))
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
        // timestamp가 빈 문자열인 행(구버전 yt-dlp fallback 등)은 crawledAt으로 대체
        let raw = (post.timestamp?.isEmpty == false) ? post.timestamp! : post.crawledAt
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
        let size = pageSize
        let sort = dbSort
        Task { @MainActor in
            do {
                // 본문 포함 첫 페이지 로드를 메인 스레드에서 돌리면 실행/새로고침마다 UI가 멈춘다.
                // 나머지는 일반 피드 스크롤 끝에서 loadMorePosts()가 페이지 단위로 이어 로드한다.
                let loaded = try await Task.detached(priority: .userInitiated) {
                    let database = try SkimDatabase(path: databasePath)
                    try database.ensureSchema()
                    return try database.loadDashboard(limit: size, sort: sort)
                }.value
                snapshot = loaded
                hasMorePosts = loaded.posts.count >= size
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

    /// 일반 피드(채널 필터가 없을 때)에서 스크롤이 끝에 닿으면 다음 페이지를 이어 로드한다.
    private func loadMorePosts() {
        guard sourceFilter == nil, hasMorePosts, !isLoadingMore else { return }
        isLoadingMore = true
        let databasePath = WorkspaceLocator.defaultDatabasePath()
        let size = pageSize
        let offset = snapshot.posts.count
        let sort = dbSort
        Task { @MainActor in
            defer { isLoadingMore = false }
            do {
                let more = try await Task.detached(priority: .userInitiated) {
                    let database = try SkimDatabase(path: databasePath)
                    try database.ensureSchema()
                    return try database.fetchRecentPosts(limit: size, offset: offset, sort: sort)
                }.value
                guard !more.isEmpty else {
                    hasMorePosts = false
                    return
                }
                snapshot = DashboardSnapshot(
                    summary: snapshot.summary,
                    posts: snapshot.posts + more,
                    sources: snapshot.sources,
                    credentials: snapshot.credentials,
                    databasePath: snapshot.databasePath
                )
                hasMorePosts = more.count >= size
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

    private func selectChannel(_ name: String) {
        let filterValue = "youtube/\(name)"
        sourceFilter = filterValue
        platformFilter = "youtube"
        reloadChannelPosts()

        // 처음 선택한 채널은 1년치 히스토리를 자동 백필한다 (upsert라 재실행 무해)
        if loadedYears[filterValue] == nil {
            loadedYears[filterValue] = 1
            runChannelBackfill(channel: name, years: 1)
        }
    }

    private func fetchChannelPosts(source: String) async throws -> [DashboardPost] {
        let databasePath = WorkspaceLocator.defaultDatabasePath()
        return try await Task.detached(priority: .userInitiated) {
            let database = try SkimDatabase(path: databasePath)
            return try database.fetchPosts(source: source)
        }.value
    }

    private func applyChannelPosts(_ posts: [DashboardPost]) {
        channelPosts = posts
        if !posts.contains(where: { $0.id == selectedPostID }) {
            selectedPostID = posts.first?.id
        }
    }

    private func reloadChannelPosts() {
        guard let source = sourceFilter else {
            return
        }
        Task { @MainActor in
            do {
                applyChannelPosts(try await fetchChannelPosts(source: source))
            } catch {
                loadError = localizedError(error)
            }
        }
    }

    private func loadMoreYears() {
        guard let sourceFilter else {
            return
        }
        let channel = String(sourceFilter.dropFirst("youtube/".count))
        let years = (loadedYears[sourceFilter] ?? 1) + 1
        loadedYears[sourceFilter] = years
        runChannelBackfill(channel: channel, years: years, autoRetry: true)
    }

    private func runChannelBackfill(channel: String, years: Int, autoRetry: Bool = false) {
        guard let source = sourceFilter else {
            return
        }
        let beforeCount = channelPosts.count
        channelBusyMessage = "\(channel) \(years)년치 영상 목록 수집 중..."
        Task { @MainActor in
            do {
                _ = try await runSkim(["youtube-history", "--channel", channel, "--years", "\(years)"])
                var posts = try await fetchChannelPosts(source: source)
                applyChannelPosts(posts)

                // 새 영상이 안 늘면 그 연도 구간이 빈 것 — 한 번 더 과거로 확장해 마지막 영상인지 확인
                if autoRetry, posts.count == beforeCount {
                    let nextYears = years + 1
                    loadedYears[source] = nextYears
                    channelBusyMessage = "\(channel) \(nextYears)년치 영상 목록 수집 중..."
                    _ = try await runSkim(["youtube-history", "--channel", channel, "--years", "\(nextYears)"])
                    posts = try await fetchChannelPosts(source: source)
                    applyChannelPosts(posts)
                    if posts.count == beforeCount {
                        exhaustedChannels.insert(source)
                    }
                }
                channelBusyMessage = nil
            } catch {
                channelBusyMessage = nil
                sourceMessage = Notice(text: localizedError(error), isError: true)
            }
        }
    }

    private func transcribe(_ post: DashboardPost) {
        guard let url = post.url else {
            return
        }
        transcribingPostID = post.id
        Task { @MainActor in
            do {
                _ = try await runSkim(["youtube-transcribe", url.absoluteString])
                reloadChannelPosts()
                loadDashboard()
            } catch {
                sourceMessage = Notice(text: localizedError(error), isError: true)
            }
            transcribingPostID = nil
        }
    }

    /// 워크스페이스 루트에서 `uv run skim <args>`를 실행한다 (앱 → 파이프라인 브리지)
    private func runSkim(_ arguments: [String]) async throws -> String {
        let workspace = WorkspaceLocator.workspaceRoot()
        return try await Task.detached(priority: .userInitiated) {
            let uvCandidates = [
                "/opt/homebrew/bin/uv",
                "/usr/local/bin/uv",
                "\(NSHomeDirectory())/.local/bin/uv"
            ]
            let uv = uvCandidates.first { FileManager.default.fileExists(atPath: $0) } ?? "uv"

            let process = Process()
            process.executableURL = URL(fileURLWithPath: uv)
            process.arguments = ["run", "skim"] + arguments
            process.currentDirectoryURL = workspace
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe
            try process.run()
            // 출력을 먼저 다 읽어야 파이프 버퍼가 차서 waitUntilExit이 멈추는 걸 막는다
            let output = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            process.waitUntilExit()
            guard process.terminationStatus == 0 else {
                throw NSError(
                    domain: "skim.cli",
                    code: Int(process.terminationStatus),
                    userInfo: [NSLocalizedDescriptionKey: String(output.suffix(300))]
                )
            }
            return output
        }.value
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
    case likes = "추천순"
    case comments = "댓글순"
}

private enum SidebarItem: Hashable {
    case all
    case platform(String)
    case channel(String)
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
