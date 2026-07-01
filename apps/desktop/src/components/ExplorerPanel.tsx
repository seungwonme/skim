import { useEffect, useMemo, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { LuExternalLink, LuSearch } from "react-icons/lu";

import { searchPosts } from "../lib/api";
import type { PostRecord, SearchFilters } from "../lib/types";

interface ExplorerPanelProps {
  report: (message: string, tone?: "neutral" | "success" | "error") => void;
}

const initialFilters: SearchFilters = {
  platform: "",
  authorQuery: "",
  keyword: "",
  startDate: "",
  endDate: "",
  minLikes: undefined,
  requireMarkdown: false,
};

const SEARCH_BATCH_SIZE = 25;

export function ExplorerPanel({ report }: ExplorerPanelProps) {
  const [draftFilters, setDraftFilters] = useState<SearchFilters>(initialFilters);
  const [appliedFilters, setAppliedFilters] = useState<SearchFilters>(initialFilters);
  const [results, setResults] = useState<PostRecord[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [resultLimit, setResultLimit] = useState(SEARCH_BATCH_SIZE);
  const [busy, setBusy] = useState(false);

  const selectedPost = useMemo(
    () => results.find((post) => post.id === selectedId) ?? null,
    [results, selectedId],
  );
  const canLoadMore = totalCount > results.length;

  function updateDraftFilters(updater: (current: SearchFilters) => SearchFilters) {
    setDraftFilters(updater);
  }

  function applyFilters(nextFilters: SearchFilters) {
    setResultLimit(SEARCH_BATCH_SIZE);
    setAppliedFilters(nextFilters);
  }

  async function loadResults(activeFilters: SearchFilters) {
    setBusy(true);
    try {
      const response = await searchPosts({
        ...activeFilters,
        platform: activeFilters.platform || undefined,
        authorQuery: activeFilters.authorQuery || undefined,
        keyword: activeFilters.keyword || undefined,
        startDate: activeFilters.startDate || undefined,
        endDate: activeFilters.endDate || undefined,
        limit: resultLimit,
      });
      setResults(response.items);
      setTotalCount(response.totalCount);
      setSelectedId((current) =>
        current !== null && response.items.some((post) => post.id === current)
          ? current
          : response.items[0]?.id ?? null,
      );
      report(
        response.totalCount > response.items.length
          ? `총 ${response.totalCount}개 중 최근 ${response.items.length}개를 불러왔습니다. 더보기로 추가 조회할 수 있습니다.`
          : `총 ${response.totalCount}개를 불러왔습니다.`,
        "neutral",
      );
    } catch (error) {
      report(`게시글 조회 실패: ${String(error)}`, "error");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void loadResults(appliedFilters);
  }, [
    appliedFilters,
    resultLimit,
  ]);

  const hasPendingChanges = useMemo(
    () => JSON.stringify(draftFilters) !== JSON.stringify(appliedFilters),
    [appliedFilters, draftFilters],
  );

  return (
    <section className="panel-stack">
      <div className="panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Explore</p>
            <h2 className="panel-title">
              <LuSearch />
              <span>Stored Posts</span>
            </h2>
          </div>
          <p className="panel-subcopy">
            기존 `data/skim.db`의 포스트를 검색합니다. 필터를 조정한 뒤 `적용`을 눌러
            조회하며, 결과는 한 번에 25개씩 추가 로드합니다.
          </p>
        </div>

        <div className="filter-grid">
          <label>
            플랫폼
            <select
              value={draftFilters.platform}
              onChange={(event) => {
                const value = event.currentTarget.value;
                updateDraftFilters((current) => ({ ...current, platform: value }));
              }}
            >
              <option value="">전체</option>
              <option value="youtube">YouTube</option>
              <option value="threads">Threads</option>
              <option value="x">X</option>
              <option value="linkedin">LinkedIn</option>
              <option value="reddit">Reddit</option>
              <option value="hackernews">HackerNews</option>
              <option value="geeknews">GeekNews</option>
            </select>
          </label>

          <label>
            작성자 또는 소스
            <input
              value={draftFilters.authorQuery ?? ""}
              onChange={(event) => {
                const value = event.currentTarget.value;
                updateDraftFilters((current) => ({ ...current, authorQuery: value }));
              }}
              placeholder="author / source"
            />
          </label>

          <label className="span-2">
            키워드
            <input
              value={draftFilters.keyword ?? ""}
              onChange={(event) => {
                const value = event.currentTarget.value;
                updateDraftFilters((current) => ({ ...current, keyword: value }));
              }}
              placeholder="title, content, summary, markdown 전체 검색"
            />
          </label>

          <label>
            시작일
            <input
              type="date"
              value={draftFilters.startDate ?? ""}
              onChange={(event) => {
                const value = event.currentTarget.value;
                updateDraftFilters((current) => ({ ...current, startDate: value }));
              }}
            />
          </label>

          <label>
            종료일
            <input
              type="date"
              value={draftFilters.endDate ?? ""}
              onChange={(event) => {
                const value = event.currentTarget.value;
                updateDraftFilters((current) => ({ ...current, endDate: value }));
              }}
            />
          </label>

          <label>
            최소 좋아요
            <input
              min={0}
              type="number"
              value={draftFilters.minLikes ?? ""}
              onChange={(event) => {
                const value = event.currentTarget.value;
                updateDraftFilters((current) => ({
                  ...current,
                  minLikes: value ? Number(value) : undefined,
                }));
              }}
            />
          </label>

          <label className="checkbox-row">
            <input
              checked={draftFilters.requireMarkdown}
              type="checkbox"
              onChange={(event) => {
                const checked = event.currentTarget.checked;
                updateDraftFilters((current) => ({
                  ...current,
                  requireMarkdown: checked,
                }));
              }}
            />
            본문 마크다운이 있는 글만 보기
          </label>

          <div className="action-row span-2">
            <button
              className="ghost-button"
              disabled={busy}
              type="button"
              onClick={() => {
                setDraftFilters(initialFilters);
              }}
            >
              필터 초기화
            </button>
            <button
              className="ghost-button icon-button"
              disabled={busy || !hasPendingChanges}
              type="button"
              onClick={() => applyFilters(draftFilters)}
            >
              <LuSearch />
              <span>적용</span>
            </button>
          </div>
        </div>
      </div>

      <div className="explorer-layout">
        <div className="panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Results</p>
              <h3>Matched Posts</h3>
            </div>
            <p className="stats-copy">
              총 {totalCount}개 중 현재 {results.length}개 로드
            </p>
          </div>

          <div className="result-list">
            {results.map((post) => (
              <button
                className={`result-card ${selectedId === post.id ? "active" : ""}`}
                key={post.id}
                type="button"
                onClick={() => setSelectedId(post.id)}
              >
                <div className="result-card-top">
                  <span className="result-platform">{post.platform}</span>
                </div>
                <strong>{post.title ?? post.author}</strong>
                <span className="row-subcopy">
                  {post.author}
                  {post.source ? ` · ${post.source}` : ""}
                </span>
                <p>{post.summary ?? post.content}</p>
              </button>
            ))}

            {canLoadMore ? (
              <div className="more-row result-list-more">
                <button
                  className="ghost-button icon-button"
                  disabled={busy}
                  type="button"
                  onClick={() => setResultLimit((current) => current + SEARCH_BATCH_SIZE)}
                >
                  <LuSearch />
                  <span>{SEARCH_BATCH_SIZE}개 더 불러오기</span>
                </button>
              </div>
            ) : null}

            {results.length === 0 ? <div className="empty-state">조건에 맞는 게시글이 없습니다.</div> : null}
          </div>
        </div>

        <div className="panel detail-panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Detail</p>
              <h3>{selectedPost?.title ?? selectedPost?.author ?? "게시글을 선택하세요"}</h3>
            </div>
            {selectedPost?.url ? (
              <button className="text-button inline-icon" type="button" onClick={() => openUrl(selectedPost.url!)}>
                <LuExternalLink />
                <span>원문 열기</span>
              </button>
            ) : null}
          </div>

          {selectedPost ? (
            <article className="detail-copy">
              <dl className="meta-grid">
                <div>
                  <dt>Platform</dt>
                  <dd>{selectedPost.platform}</dd>
                </div>
                <div>
                  <dt>Author</dt>
                  <dd>{selectedPost.author}</dd>
                </div>
                <div>
                  <dt>Timestamp</dt>
                  <dd>{selectedPost.timestamp ?? "-"}</dd>
                </div>
                <div>
                  <dt>Crawled</dt>
                  <dd>{selectedPost.crawledAt}</dd>
                </div>
                <div>
                  <dt>Likes</dt>
                  <dd>{selectedPost.likes ?? 0}</dd>
                </div>
                <div>
                  <dt>Comments</dt>
                  <dd>{selectedPost.comments ?? 0}</dd>
                </div>
              </dl>

              <section>
                <h4>Summary</h4>
                <p>{selectedPost.summary ?? "요약이 저장되지 않았습니다."}</p>
              </section>

              <section>
                <h4>Content</h4>
                <pre>{selectedPost.content}</pre>
              </section>

              {selectedPost.contentMarkdown ? (
                <section>
                  <h4>Enriched Markdown</h4>
                  <pre>{selectedPost.contentMarkdown}</pre>
                </section>
              ) : null}
            </article>
          ) : (
            <div className="empty-state">좌측 결과에서 게시글을 선택하면 상세 내용이 표시됩니다.</div>
          )}
        </div>
      </div>
    </section>
  );
}
