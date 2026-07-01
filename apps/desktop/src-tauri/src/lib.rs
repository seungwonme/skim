use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::SystemTime;

use chrono::{DateTime, FixedOffset, Utc};
use rusqlite::types::Value;
use rusqlite::{params, params_from_iter, Connection, OptionalExtension, Row};
use serde::{Deserialize, Serialize};

const APP_SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS posts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    platform         TEXT NOT NULL,
    source           TEXT,
    external_id      TEXT,
    author           TEXT NOT NULL,
    title            TEXT,
    content          TEXT NOT NULL,
    url              TEXT,
    timestamp        TEXT,
    likes            INTEGER,
    comments         INTEGER,
    reposts          INTEGER,
    views            INTEGER,
    summary          TEXT,
    content_markdown TEXT,
    word_count       INTEGER,
    extra            TEXT,
    crawled_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(platform, external_id)
);

CREATE TABLE IF NOT EXISTS summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     INTEGER NOT NULL REFERENCES posts(id),
    model       TEXT NOT NULL,
    summary     TEXT NOT NULL,
    tags        TEXT,
    relevance   REAL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     INTEGER NOT NULL REFERENCES posts(id),
    action      TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    posts_count INTEGER DEFAULT 0,
    summary     TEXT,
    current_platform TEXT,
    runner_pid  INTEGER,
    runner_host TEXT
);

CREATE TABLE IF NOT EXISTS tracked_sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    canonical_id  TEXT NOT NULL,
    handle_or_url TEXT,
    is_enabled    INTEGER NOT NULL DEFAULT 1,
    focus_level   INTEGER NOT NULL DEFAULT 0,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(platform, canonical_id)
);

CREATE TABLE IF NOT EXISTS platform_credentials (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    platform         TEXT NOT NULL,
    account_label    TEXT NOT NULL,
    login_identifier TEXT NOT NULL,
    secret_service   TEXT NOT NULL,
    secret_account   TEXT NOT NULL,
    session_path     TEXT,
    session_status   TEXT NOT NULL DEFAULT 'missing',
    last_verified_at TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(platform, login_identifier)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_posts_platform    ON posts(platform);
CREATE INDEX IF NOT EXISTS idx_posts_crawled_at  ON posts(crawled_at);
CREATE INDEX IF NOT EXISTS idx_posts_platform_url ON posts(platform, url)
    WHERE url IS NOT NULL AND TRIM(url) <> '';
CREATE INDEX IF NOT EXISTS idx_summaries_post_id ON summaries(post_id);
CREATE INDEX IF NOT EXISTS idx_feedback_post_id  ON feedback(post_id);
CREATE INDEX IF NOT EXISTS idx_feedback_action   ON feedback(action);
CREATE INDEX IF NOT EXISTS idx_tracked_sources_platform ON tracked_sources(platform);
CREATE INDEX IF NOT EXISTS idx_tracked_sources_enabled  ON tracked_sources(is_enabled);
CREATE INDEX IF NOT EXISTS idx_credentials_platform     ON platform_credentials(platform);
"#;

const DEFAULT_POST_SEARCH_LIMIT: i64 = 25;
const MAX_POST_SEARCH_LIMIT: i64 = 200;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct AppOverview {
    workspace_root: String,
    db_path: String,
    session_dir: String,
    posts_count: i64,
    tracked_sources_count: i64,
    credentials_count: i64,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TrackedSourceInput {
    id: Option<i64>,
    platform: String,
    source_type: String,
    display_name: String,
    canonical_id: String,
    handle_or_url: Option<String>,
    is_enabled: bool,
    focus_level: i64,
    notes: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct TrackedSource {
    id: i64,
    platform: String,
    source_type: String,
    display_name: String,
    canonical_id: String,
    handle_or_url: Option<String>,
    is_enabled: bool,
    focus_level: i64,
    notes: Option<String>,
    created_at: String,
    updated_at: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CredentialInput {
    id: Option<i64>,
    platform: String,
    account_label: String,
    login_identifier: String,
    password: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct PlatformCredential {
    id: i64,
    platform: String,
    account_label: String,
    login_identifier: String,
    secret_service: String,
    secret_account: String,
    session_path: Option<String>,
    session_status: String,
    last_verified_at: Option<String>,
    created_at: String,
    updated_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct SessionStatus {
    platform: String,
    path: String,
    status: String,
    cookie_count: usize,
    modified_at: Option<String>,
    age_days: Option<i64>,
}

#[derive(Debug, Serialize, Deserialize, Default, Clone)]
#[serde(rename_all = "camelCase")]
struct SearchFilters {
    platform: Option<String>,
    author_query: Option<String>,
    keyword: Option<String>,
    start_date: Option<String>,
    end_date: Option<String>,
    min_likes: Option<i64>,
    require_markdown: bool,
    limit: Option<i64>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct PostRecord {
    id: i64,
    platform: String,
    source: Option<String>,
    external_id: Option<String>,
    author: String,
    title: Option<String>,
    content: String,
    url: Option<String>,
    timestamp: Option<String>,
    likes: Option<i64>,
    comments: Option<i64>,
    reposts: Option<i64>,
    views: Option<i64>,
    summary: Option<String>,
    content_markdown: Option<String>,
    word_count: Option<i64>,
    extra: Option<String>,
    crawled_at: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct SearchPostsResult {
    items: Vec<PostRecord>,
    total_count: i64,
}

#[derive(Debug, Serialize, Deserialize)]
struct FeedImportItem {
    display_name: String,
    canonical_id: String,
    handle_or_url: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct FeedImportResult {
    platform: String,
    total: usize,
    new_count: usize,
    skipped_count: usize,
    inserted_count: Option<usize>,
    items: Vec<FeedImportItem>,
}

fn workspace_root() -> Result<PathBuf, String> {
    if let Ok(override_root) = std::env::var("SKIM_WORKSPACE_ROOT") {
        return Ok(PathBuf::from(override_root));
    }

    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .map_err(|error| format!("workspace root를 찾을 수 없습니다: {error}"))
}

fn db_path() -> Result<PathBuf, String> {
    Ok(workspace_root()?.join("data").join("skim.db"))
}

fn sessions_dir() -> Result<PathBuf, String> {
    Ok(workspace_root()?.join("data").join("sessions"))
}

fn ensure_database() -> Result<Connection, String> {
    let path = db_path()?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|error| format!("data 디렉터리 생성 실패: {error}"))?;
    }

    let conn = Connection::open(&path).map_err(|error| format!("SQLite 연결 실패: {error}"))?;
    conn.execute_batch(
        "
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        ",
    )
    .map_err(|error| format!("SQLite PRAGMA 초기화 실패: {error}"))?;
    conn.execute_batch(APP_SCHEMA)
        .map_err(|error| format!("앱 스키마 초기화 실패: {error}"))?;
    ensure_runs_columns(&conn)?;
    Ok(conn)
}

fn ensure_runs_columns(conn: &Connection) -> Result<(), String> {
    let mut statement = conn
        .prepare("PRAGMA table_info(runs)")
        .map_err(|error| format!("runs 스키마 조회 실패: {error}"))?;
    let column_iter = statement
        .query_map([], |row| row.get::<_, String>("name"))
        .map_err(|error| format!("runs 컬럼 조회 실패: {error}"))?;

    let columns = column_iter
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("runs 컬럼 변환 실패: {error}"))?;

    for (name, definition) in [
        ("current_platform", "TEXT"),
        ("runner_pid", "INTEGER"),
        ("runner_host", "TEXT"),
    ] {
        if columns.iter().any(|column| column == name) {
            continue;
        }
        let sql = format!("ALTER TABLE runs ADD COLUMN {name} {definition}");
        conn.execute(&sql, [])
            .map_err(|error| format!("runs 컬럼 `{name}` 추가 실패: {error}"))?;
    }

    Ok(())
}

fn count_rows(conn: &Connection, table: &str) -> Result<i64, String> {
    let sql = format!("SELECT COUNT(*) FROM {table}");
    conn.query_row(&sql, [], |row| row.get(0))
        .map_err(|error| format!("{table} 카운트 조회 실패: {error}"))
}

fn bool_to_sql(value: bool) -> i64 {
    if value { 1 } else { 0 }
}

fn resolve_post_search_limit(limit: Option<i64>) -> i64 {
    match limit {
        Some(value) if value > 0 => value.min(MAX_POST_SEARCH_LIMIT),
        _ => DEFAULT_POST_SEARCH_LIMIT,
    }
}

fn append_post_search_filters(sql: &mut String, values: &mut Vec<Value>, filters: &SearchFilters) {
    if let Some(platform) = filters.platform.clone().filter(|value| !value.trim().is_empty()) {
        sql.push_str(" AND platform = ?");
        values.push(Value::Text(platform));
    }

    if let Some(author_query) = filters
        .author_query
        .clone()
        .filter(|value| !value.trim().is_empty())
    {
        let pattern = format!("%{}%", author_query.to_lowercase());
        sql.push_str(" AND (LOWER(author) LIKE ? OR LOWER(IFNULL(source, '')) LIKE ?)");
        values.push(Value::Text(pattern.clone()));
        values.push(Value::Text(pattern));
    }

    if let Some(keyword) = filters.keyword.clone().filter(|value| !value.trim().is_empty()) {
        let pattern = format!("%{}%", keyword.to_lowercase());
        sql.push_str(
            " AND (
                LOWER(IFNULL(title, '')) LIKE ?
                OR LOWER(content) LIKE ?
                OR LOWER(IFNULL(summary, '')) LIKE ?
                OR LOWER(IFNULL(content_markdown, '')) LIKE ?
            )",
        );
        values.push(Value::Text(pattern.clone()));
        values.push(Value::Text(pattern.clone()));
        values.push(Value::Text(pattern.clone()));
        values.push(Value::Text(pattern));
    }

    if let Some(start_date) = filters.start_date.clone().filter(|value| !value.trim().is_empty()) {
        sql.push_str(" AND date(crawled_at) >= date(?)");
        values.push(Value::Text(start_date));
    }

    if let Some(end_date) = filters.end_date.clone().filter(|value| !value.trim().is_empty()) {
        sql.push_str(" AND date(crawled_at) <= date(?)");
        values.push(Value::Text(end_date));
    }

    if let Some(min_likes) = filters.min_likes {
        sql.push_str(" AND COALESCE(likes, 0) >= ?");
        values.push(Value::Integer(min_likes));
    }

    if filters.require_markdown {
        sql.push_str(" AND content_markdown IS NOT NULL AND TRIM(content_markdown) <> ''");
    }
}

fn map_tracked_source(row: &Row<'_>) -> rusqlite::Result<TrackedSource> {
    Ok(TrackedSource {
        id: row.get("id")?,
        platform: row.get("platform")?,
        source_type: row.get("source_type")?,
        display_name: row.get("display_name")?,
        canonical_id: row.get("canonical_id")?,
        handle_or_url: row.get("handle_or_url")?,
        is_enabled: row.get::<_, i64>("is_enabled")? == 1,
        focus_level: row.get("focus_level")?,
        notes: row.get("notes")?,
        created_at: row.get("created_at")?,
        updated_at: row.get("updated_at")?,
    })
}

fn map_post(row: &Row<'_>) -> rusqlite::Result<PostRecord> {
    Ok(PostRecord {
        id: row.get("id")?,
        platform: row.get("platform")?,
        source: row.get("source")?,
        external_id: row.get("external_id")?,
        author: row.get("author")?,
        title: row.get("title")?,
        content: row.get("content")?,
        url: row.get("url")?,
        timestamp: row.get("timestamp")?,
        likes: row.get("likes")?,
        comments: row.get("comments")?,
        reposts: row.get("reposts")?,
        views: row.get("views")?,
        summary: row.get("summary")?,
        content_markdown: row.get("content_markdown")?,
        word_count: row.get("word_count")?,
        extra: row.get("extra")?,
        crawled_at: row.get("crawled_at")?,
    })
}

fn resolve_session_path(workspace_root: &Path, platform: &str, session_path: Option<&str>) -> PathBuf {
    if let Some(explicit_path) = session_path {
        workspace_root.join(explicit_path)
    } else {
        workspace_root
            .join("data")
            .join("sessions")
            .join(format!("{platform}_session.json"))
    }
}

fn format_system_time_in_seoul(time: SystemTime) -> Option<String> {
    let seoul_offset = FixedOffset::east_opt(9 * 60 * 60)?;
    Some(
        DateTime::<Utc>::from(time)
            .with_timezone(&seoul_offset)
            .format("%Y-%m-%d %H:%M:%S KST")
            .to_string(),
    )
}

fn delete_session_file(workspace_root: &Path, session_path: Option<&str>) -> Result<(), String> {
    let Some(session_path) = session_path.filter(|value| !value.trim().is_empty()) else {
        return Ok(());
    };

    let resolved_path = workspace_root.join(session_path);
    if !resolved_path.exists() {
        return Ok(());
    }

    fs::remove_file(&resolved_path)
        .map_err(|error| format!("세션 파일 삭제 실패 ({}): {error}", resolved_path.display()))
}

fn inspect_session_file(platform: &str, session_path: Option<&str>) -> Result<SessionStatus, String> {
    let root = workspace_root()?;
    let path = resolve_session_path(&root, platform, session_path);

    if !path.exists() {
        return Ok(SessionStatus {
            platform: platform.to_string(),
            path: path.display().to_string(),
            status: "missing".to_string(),
            cookie_count: 0,
            modified_at: None,
            age_days: None,
        });
    }

    let content = fs::read_to_string(&path)
        .map_err(|error| format!("세션 파일 읽기 실패 ({}): {error}", path.display()))?;
    let value: serde_json::Value =
        serde_json::from_str(&content).map_err(|error| format!("세션 파일 파싱 실패: {error}"))?;
    let cookie_count = value
        .get("cookies")
        .and_then(serde_json::Value::as_array)
        .map_or(0, Vec::len);

    let metadata = fs::metadata(&path).map_err(|error| format!("세션 메타데이터 읽기 실패: {error}"))?;
    let modified = metadata
        .modified()
        .ok()
        .and_then(format_system_time_in_seoul);
    let age_days = metadata
        .modified()
        .ok()
        .map(|time| SystemTime::now().duration_since(time).unwrap_or_default().as_secs() / 86_400)
        .map(|days| days as i64);

    let status = if cookie_count == 0 {
        "invalid"
    } else if age_days.unwrap_or_default() > 14 {
        "expired"
    } else {
        "healthy"
    };

    Ok(SessionStatus {
        platform: platform.to_string(),
        path: path.display().to_string(),
        status: status.to_string(),
        cookie_count,
        modified_at: modified,
        age_days,
    })
}

fn keychain_secret_service(platform: &str) -> String {
    format!("skim.desktop.{platform}")
}

fn save_secret_mac(service: &str, account: &str, password: &str) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let output = Command::new("security")
            .args([
                "add-generic-password",
                "-U",
                "-a",
                account,
                "-s",
                service,
                "-w",
                password,
            ])
            .output()
            .map_err(|error| format!("macOS Keychain 저장 실패: {error}"))?;

        if output.status.success() {
            return Ok(());
        }

        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        Err(format!("macOS Keychain 저장 실패: {stderr}"))
    }

    #[cfg(not(target_os = "macos"))]
    {
        let _ = (service, account, password);
        Err("현재 빌드는 macOS Keychain만 지원합니다.".to_string())
    }
}

fn delete_secret_mac(service: &str, account: &str) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let output = Command::new("security")
            .args(["delete-generic-password", "-a", account, "-s", service])
            .output()
            .map_err(|error| format!("macOS Keychain 삭제 실패: {error}"))?;

        if output.status.success() {
            return Ok(());
        }

        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        if stderr.contains("could not be found") {
            return Ok(());
        }

        Err(format!("macOS Keychain 삭제 실패: {stderr}"))
    }

    #[cfg(not(target_os = "macos"))]
    {
        let _ = (service, account);
        Err("현재 빌드는 macOS Keychain만 지원합니다.".to_string())
    }
}

fn run_python_json(args: &[&str]) -> Result<FeedImportResult, String> {
    let root = workspace_root()?;
    let output = Command::new("uv")
        .args(args)
        .current_dir(&root)
        .output()
        .map_err(|error| format!("Python bridge 실행 실패: {error}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(format!("Python bridge 실패: {stderr}"));
    }

    serde_json::from_slice(&output.stdout).map_err(|error| format!("Python JSON 응답 파싱 실패: {error}"))
}

#[tauri::command]
fn get_app_overview() -> Result<AppOverview, String> {
    let conn = ensure_database()?;
    Ok(AppOverview {
        workspace_root: workspace_root()?.display().to_string(),
        db_path: db_path()?.display().to_string(),
        session_dir: sessions_dir()?.display().to_string(),
        posts_count: count_rows(&conn, "posts")?,
        tracked_sources_count: count_rows(&conn, "tracked_sources")?,
        credentials_count: count_rows(&conn, "platform_credentials")?,
    })
}

#[tauri::command]
fn list_tracked_sources() -> Result<Vec<TrackedSource>, String> {
    let conn = ensure_database()?;
    let mut statement = conn
        .prepare(
            "SELECT id, platform, source_type, display_name, canonical_id, handle_or_url,
                    is_enabled, focus_level, notes, created_at, updated_at
             FROM tracked_sources
             ORDER BY platform, focus_level DESC, display_name COLLATE NOCASE ASC",
        )
        .map_err(|error| format!("tracked_sources 조회 준비 실패: {error}"))?;
    let rows = statement
        .query_map([], map_tracked_source)
        .map_err(|error| format!("tracked_sources 조회 실패: {error}"))?;

    rows.collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("tracked_sources 변환 실패: {error}"))
}

#[tauri::command]
fn upsert_tracked_source(input: TrackedSourceInput) -> Result<TrackedSource, String> {
    let conn = ensure_database()?;

    if let Some(id) = input.id {
        conn.execute(
            "
            UPDATE tracked_sources
            SET platform = ?1,
                source_type = ?2,
                display_name = ?3,
                canonical_id = ?4,
                handle_or_url = ?5,
                is_enabled = ?6,
                focus_level = ?7,
                notes = ?8,
                updated_at = datetime('now')
            WHERE id = ?9
            ",
            params![
                input.platform,
                input.source_type,
                input.display_name,
                input.canonical_id,
                input.handle_or_url,
                bool_to_sql(input.is_enabled),
                input.focus_level,
                input.notes,
                id
            ],
        )
        .map_err(|error| format!("tracked_source 수정 실패: {error}"))?;

        let tracked = conn
            .query_row(
                "
                SELECT id, platform, source_type, display_name, canonical_id, handle_or_url,
                       is_enabled, focus_level, notes, created_at, updated_at
                FROM tracked_sources
                WHERE id = ?1
                ",
                [id],
                map_tracked_source,
            )
            .map_err(|error| format!("수정된 tracked_source 조회 실패: {error}"))?;
        return Ok(tracked);
    }

    conn.execute(
        "
        INSERT INTO tracked_sources (
            platform,
            source_type,
            display_name,
            canonical_id,
            handle_or_url,
            is_enabled,
            focus_level,
            notes
        ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
        ",
        params![
            input.platform,
            input.source_type,
            input.display_name,
            input.canonical_id,
            input.handle_or_url,
            bool_to_sql(input.is_enabled),
            input.focus_level,
            input.notes
        ],
    )
    .map_err(|error| format!("tracked_source 생성 실패: {error}"))?;

    let id = conn.last_insert_rowid();
    conn.query_row(
        "
        SELECT id, platform, source_type, display_name, canonical_id, handle_or_url,
               is_enabled, focus_level, notes, created_at, updated_at
        FROM tracked_sources
        WHERE id = ?1
        ",
        [id],
        map_tracked_source,
    )
    .map_err(|error| format!("생성된 tracked_source 조회 실패: {error}"))
}

#[tauri::command]
fn delete_tracked_source(id: i64) -> Result<(), String> {
    let conn = ensure_database()?;
    conn.execute("DELETE FROM tracked_sources WHERE id = ?1", [id])
        .map_err(|error| format!("tracked_source 삭제 실패: {error}"))?;
    Ok(())
}

#[tauri::command]
fn list_credentials() -> Result<Vec<PlatformCredential>, String> {
    let conn = ensure_database()?;
    let mut statement = conn
        .prepare(
            "
            SELECT id, platform, account_label, login_identifier, secret_service, secret_account,
                   session_path, session_status, last_verified_at, created_at, updated_at
            FROM platform_credentials
            ORDER BY platform, account_label COLLATE NOCASE ASC
            ",
        )
        .map_err(|error| format!("credential 조회 준비 실패: {error}"))?;

    let rows = statement
        .query_map([], |row| {
            let platform: String = row.get("platform")?;
            let session_path: Option<String> = row.get("session_path")?;
            let session = inspect_session_file(&platform, session_path.as_deref())
                .unwrap_or(SessionStatus {
                    platform: platform.clone(),
                    path: session_path.clone().unwrap_or_default(),
                    status: "invalid".to_string(),
                    cookie_count: 0,
                    modified_at: None,
                    age_days: None,
                });

            Ok(PlatformCredential {
                id: row.get("id")?,
                platform,
                account_label: row.get("account_label")?,
                login_identifier: row.get("login_identifier")?,
                secret_service: row.get("secret_service")?,
                secret_account: row.get("secret_account")?,
                session_path,
                session_status: session.status,
                last_verified_at: session.modified_at,
                created_at: row.get("created_at")?,
                updated_at: row.get("updated_at")?,
            })
        })
        .map_err(|error| format!("credential 조회 실패: {error}"))?;

    rows.collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("credential 변환 실패: {error}"))
}

#[tauri::command]
fn save_credential(input: CredentialInput) -> Result<PlatformCredential, String> {
    let service = keychain_secret_service(&input.platform);
    let account = input.login_identifier.clone();
    save_secret_mac(&service, &account, &input.password)?;

    let conn = ensure_database()?;
    let session_path = format!("data/sessions/{}_session.json", input.platform);

    if let Some(id) = input.id {
        conn.execute(
            "
            UPDATE platform_credentials
            SET platform = ?1,
                account_label = ?2,
                login_identifier = ?3,
                secret_service = ?4,
                secret_account = ?5,
                session_path = ?6,
                updated_at = datetime('now')
            WHERE id = ?7
            ",
            params![
                input.platform,
                input.account_label,
                input.login_identifier,
                service,
                account,
                session_path,
                id
            ],
        )
        .map_err(|error| format!("credential 수정 실패: {error}"))?;
    } else {
        conn.execute(
            "
            INSERT INTO platform_credentials (
                platform,
                account_label,
                login_identifier,
                secret_service,
                secret_account,
                session_path
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)
            ON CONFLICT(platform, login_identifier) DO UPDATE SET
                account_label = excluded.account_label,
                secret_service = excluded.secret_service,
                secret_account = excluded.secret_account,
                session_path = excluded.session_path,
                updated_at = datetime('now')
            ",
            params![
                input.platform,
                input.account_label,
                input.login_identifier,
                service,
                account,
                session_path
            ],
        )
        .map_err(|error| format!("credential 저장 실패: {error}"))?;
    }

    let credential = conn
        .query_row(
            "
            SELECT id, platform, account_label, login_identifier, secret_service, secret_account,
                   session_path, session_status, last_verified_at, created_at, updated_at
            FROM platform_credentials
            WHERE platform = ?1 AND login_identifier = ?2
            ",
            params![input.platform, input.login_identifier],
            |row| {
                Ok(PlatformCredential {
                    id: row.get("id")?,
                    platform: row.get("platform")?,
                    account_label: row.get("account_label")?,
                    login_identifier: row.get("login_identifier")?,
                    secret_service: row.get("secret_service")?,
                    secret_account: row.get("secret_account")?,
                    session_path: row.get("session_path")?,
                    session_status: row.get("session_status")?,
                    last_verified_at: row.get("last_verified_at")?,
                    created_at: row.get("created_at")?,
                    updated_at: row.get("updated_at")?,
                })
            },
        )
        .map_err(|error| format!("저장된 credential 조회 실패: {error}"))?;

    Ok(credential)
}

#[tauri::command]
fn delete_credential(id: i64) -> Result<(), String> {
    let conn = ensure_database()?;
    let credential: Option<(String, String, Option<String>)> = conn
        .query_row(
            "SELECT secret_service, secret_account, session_path FROM platform_credentials WHERE id = ?1",
            [id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .optional()
        .map_err(|error| format!("credential 조회 실패: {error}"))?;

    if let Some((service, account, session_path)) = credential {
        delete_secret_mac(&service, &account)?;
        delete_session_file(&workspace_root()?, session_path.as_deref())?;
    }

    conn.execute("DELETE FROM platform_credentials WHERE id = ?1", [id])
        .map_err(|error| format!("credential 삭제 실패: {error}"))?;
    Ok(())
}

#[tauri::command]
fn verify_session(platform: String, session_path: Option<String>) -> Result<SessionStatus, String> {
    inspect_session_file(&platform, session_path.as_deref())
}

#[tauri::command]
fn start_login(credential_id: i64) -> Result<String, String> {
    let root = workspace_root()?;
    let conn = ensure_database()?;
    let credential = conn
        .query_row(
            "
            SELECT platform, account_label, login_identifier
            FROM platform_credentials
            WHERE id = ?1
            ",
            [credential_id],
            |row| {
                Ok((
                    row.get::<_, String>("platform")?,
                    row.get::<_, String>("account_label")?,
                    row.get::<_, String>("login_identifier")?,
                ))
            },
        )
        .optional()
        .map_err(|error| format!("로그인 credential 조회 실패: {error}"))?
        .ok_or_else(|| format!("ID {credential_id}에 해당하는 credential이 없습니다."))?;

    let (platform, account_label, login_identifier) = credential;

    Command::new("uv")
        .args(["run", "skim", "login", &platform, "--identifier", &login_identifier])
        .current_dir(root)
        .spawn()
        .map_err(|error| format!("로그인 프로세스 실행 실패: {error}"))?;

    Ok(format!(
        "{platform} 로그인 프로세스를 시작했습니다. 저장된 `{account_label}` 계정으로 자동 입력을 시도합니다."
    ))
}

#[tauri::command]
fn preview_feed_import() -> Result<FeedImportResult, String> {
    run_python_json(&["run", "python", "scripts/import_feed_config.py", "--preview"])
}

#[tauri::command]
fn import_feed_sources() -> Result<FeedImportResult, String> {
    run_python_json(&["run", "python", "scripts/import_feed_config.py"])
}

#[tauri::command]
fn search_posts(filters: SearchFilters) -> Result<SearchPostsResult, String> {
    let conn = ensure_database()?;
    let limit = resolve_post_search_limit(filters.limit);
    let mut count_sql = String::from(
        "
        SELECT COUNT(*)
        FROM posts
        WHERE 1 = 1
        ",
    );
    let mut count_values: Vec<Value> = Vec::new();
    append_post_search_filters(&mut count_sql, &mut count_values, &filters);
    let total_count = conn
        .query_row(&count_sql, params_from_iter(count_values), |row| row.get(0))
        .map_err(|error| format!("posts 총 개수 조회 실패: {error}"))?;

    let mut sql = String::from(
        "
        SELECT id, platform, source, external_id, author, title, content, url, timestamp,
               likes, comments, reposts, views, summary, content_markdown, word_count, extra, crawled_at
        FROM posts
        WHERE 1 = 1
        ",
    );
    let mut values: Vec<Value> = Vec::new();
    append_post_search_filters(&mut sql, &mut values, &filters);

    sql.push_str(" ORDER BY crawled_at DESC LIMIT ?");
    values.push(Value::Integer(limit));

    let mut statement = conn
        .prepare(&sql)
        .map_err(|error| format!("posts 검색 준비 실패: {error}"))?;
    let rows = statement
        .query_map(params_from_iter(values), map_post)
        .map_err(|error| format!("posts 검색 실패: {error}"))?;

    let items = rows
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("posts 검색 변환 실패: {error}"))?;

    Ok(SearchPostsResult { items, total_count })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            get_app_overview,
            list_tracked_sources,
            upsert_tracked_source,
            delete_tracked_source,
            list_credentials,
            save_credential,
            delete_credential,
            verify_session,
            start_login,
            preview_feed_import,
            import_feed_sources,
            search_posts
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::Mutex;
    use std::time::{Duration, UNIX_EPOCH};

    static WORKSPACE_ROOT_MUTEX: Mutex<()> = Mutex::new(());

    fn with_test_workspace<T>(name: &str, run: impl FnOnce(&Path) -> T) -> T {
        let _guard = WORKSPACE_ROOT_MUTEX
            .lock()
            .expect("workspace mutex should lock");

        let root = std::env::temp_dir().join(format!(
            "skim-desktop-{name}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("time should move forward")
                .as_nanos()
        ));
        fs::create_dir_all(root.join("data")).expect("workspace data directory should exist");

        std::env::set_var("SKIM_WORKSPACE_ROOT", &root);
        let result = run(&root);
        std::env::remove_var("SKIM_WORKSPACE_ROOT");

        let _ = fs::remove_dir_all(&root);
        result
    }

    fn seed_post(conn: &Connection, id: i64) {
        conn.execute(
            "
            INSERT INTO posts (
                platform, source, external_id, author, title, content, url, timestamp,
                likes, comments, reposts, views, summary, content_markdown, word_count, extra, crawled_at
            ) VALUES (
                ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8,
                ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17
            )
            ",
            params![
                "youtube",
                format!("source-{id}"),
                format!("external-{id}"),
                format!("author-{id}"),
                format!("title-{id}"),
                format!("content-{id}"),
                format!("https://example.com/{id}"),
                format!("2026-04-08T00:{id:02}:00Z"),
                id,
                id,
                id,
                id,
                format!("summary-{id}"),
                format!("markdown-{id}"),
                100 + id,
                "{}",
                format!("2026-04-08 00:{id:02}:00"),
            ],
        )
        .expect("post should be inserted");
    }

    #[test]
    fn format_modified_time_as_kst() {
        let formatted = format_system_time_in_seoul(UNIX_EPOCH + Duration::from_secs(0))
            .expect("time should format");

        assert_eq!(formatted, "1970-01-01 09:00:00 KST");
    }

    #[test]
    fn delete_session_file_removes_file_from_workspace_relative_path() {
        let root = std::env::temp_dir().join(format!(
            "skim-delete-session-{}",
            std::process::id()
        ));
        let session_path = root.join("data/sessions/test_session.json");
        fs::create_dir_all(session_path.parent().expect("parent should exist"))
            .expect("directories should be created");
        fs::write(&session_path, "{}").expect("session file should be written");

        delete_session_file(&root, Some("data/sessions/test_session.json"))
            .expect("session file should be removed");

        assert!(!session_path.exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn search_posts_applies_default_limit_for_empty_filters() {
        with_test_workspace("search-default-limit", |_| {
            let conn = ensure_database().expect("database should initialize");
            for id in 1..=30 {
                seed_post(&conn, id);
            }

            let result = search_posts(SearchFilters::default())
                .expect("search should succeed");

            assert_eq!(
                result.items.len(),
                25,
                "empty Explorer query should stay capped to the first page"
            );
            assert_eq!(
                result.total_count,
                30,
                "Explorer should still know the full match count beyond the first page"
            );
        });
    }

    #[test]
    fn get_app_overview_initializes_fresh_workspace_database() {
        with_test_workspace("overview-fresh-db", |root| {
            let overview = get_app_overview().expect("overview should succeed on a fresh workspace");

            assert_eq!(overview.workspace_root, root.display().to_string());
            assert_eq!(overview.posts_count, 0);
            assert_eq!(overview.tracked_sources_count, 0);
            assert_eq!(overview.credentials_count, 0);
            assert!(
                root.join("data/skim.db").exists(),
                "desktop startup should create a workspace-local SQLite file"
            );
        });
    }

    #[test]
    fn search_posts_returns_empty_result_on_fresh_workspace_database() {
        with_test_workspace("search-fresh-db", |_| {
            let result =
                search_posts(SearchFilters::default()).expect("search should succeed on a fresh db");

            assert_eq!(result.total_count, 0);
            assert!(result.items.is_empty());
        });
    }
}
