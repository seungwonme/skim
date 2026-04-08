import { useMemo, useState } from "react";
import {
  LuBadgeCheck,
  LuCircleOff,
  LuImport,
  LuPanelsTopLeft,
  LuPlus,
  LuRows3,
  LuTrash2,
} from "react-icons/lu";

import { deleteTrackedSource, importFeedSources, previewFeedImport, upsertTrackedSource } from "../lib/api";
import type { FeedImportResult, TrackedSource, TrackedSourceInput } from "../lib/types";

interface SourcesPanelProps {
  sources: TrackedSource[];
  onChanged: () => Promise<void>;
  report: (message: string, tone?: "neutral" | "success" | "error") => void;
}

const emptyForm: TrackedSourceInput = {
  platform: "youtube",
  sourceType: "channel",
  displayName: "",
  canonicalId: "",
  handleOrUrl: "",
  isEnabled: true,
  focusLevel: 0,
  notes: "",
};

export function SourcesPanel({ sources, onChanged, report }: SourcesPanelProps) {
  const [form, setForm] = useState<TrackedSourceInput>(emptyForm);
  const [importPreview, setImportPreview] = useState<FeedImportResult | null>(null);
  const [visibleCount, setVisibleCount] = useState(6);
  const [busy, setBusy] = useState(false);

  const sortedSources = useMemo(
    () =>
      [...sources].sort((left, right) => {
        if (left.focusLevel !== right.focusLevel) {
          return right.focusLevel - left.focusLevel;
        }
        return left.displayName.localeCompare(right.displayName);
      }),
    [sources],
  );

  const previewSources = useMemo(
    () => sortedSources.slice(0, visibleCount),
    [sortedSources, visibleCount],
  );

  async function handleSave(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      await upsertTrackedSource({
        ...form,
        handleOrUrl: form.handleOrUrl || null,
        notes: form.notes || null,
      });
      report(form.id ? "수집 대상을 수정했습니다." : "수집 대상을 추가했습니다.", "success");
      setForm(emptyForm);
      setVisibleCount((current) => Math.max(current, 6));
      await onChanged();
    } catch (error) {
      report(`수집 대상 저장 실패: ${String(error)}`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(id: number) {
    if (!window.confirm("이 수집 대상을 삭제할까요?")) {
      return;
    }

    setBusy(true);
    try {
      await deleteTrackedSource(id);
      report("수집 대상을 삭제했습니다.", "success");
      await onChanged();
    } catch (error) {
      report(`수집 대상 삭제 실패: ${String(error)}`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function handlePreviewImport() {
    setBusy(true);
    try {
      const preview = await previewFeedImport();
      setImportPreview(preview);
      report(
        `기존 feed_config 미리보기를 불러왔습니다. 신규 ${preview.newCount}개, 중복 ${preview.skippedCount}개입니다.`,
        "neutral",
      );
    } catch (error) {
      report(`feed_config 미리보기 실패: ${String(error)}`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function handleImport() {
    setBusy(true);
    try {
      const result = await importFeedSources();
      setImportPreview(result);
      report(`feed_config에서 ${result.insertedCount ?? 0}개를 가져왔습니다.`, "success");
      await onChanged();
    } catch (error) {
      report(`feed_config 가져오기 실패: ${String(error)}`, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel-stack">
      <div className="panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Settings</p>
            <h2 className="panel-title">
              <LuPanelsTopLeft />
              <span>Tracked Sources</span>
            </h2>
          </div>
          <p className="panel-subcopy">
            YouTube 채널과 Threads/X/LinkedIn/Reddit 추적 대상을 로컬 DB에 저장합니다.
          </p>
        </div>

        <form className="form-grid" onSubmit={handleSave}>
          <label>
            플랫폼
            <select
              value={form.platform}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setForm((current) => ({
                  ...current,
                  platform: value,
                  sourceType:
                    value === "youtube"
                      ? "channel"
                      : value === "reddit"
                        ? "subreddit"
                        : "account",
                }));
              }}
            >
              <option value="youtube">YouTube</option>
              <option value="threads">Threads</option>
              <option value="x">X</option>
              <option value="linkedin">LinkedIn</option>
              <option value="reddit">Reddit</option>
            </select>
          </label>

          <label>
            유형
            <input
              value={form.sourceType}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setForm((current) => ({ ...current, sourceType: value }));
              }}
              placeholder="channel / account / profile"
            />
          </label>

          <label>
            표시 이름
            <input
              required
              value={form.displayName}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setForm((current) => ({ ...current, displayName: value }));
              }}
              placeholder="예: EO Global"
            />
          </label>

          <label>
            정규화 ID
            <input
              required
              value={form.canonicalId}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setForm((current) => ({ ...current, canonicalId: value }));
              }}
              placeholder="예: channel_id, handle, profile key"
            />
          </label>

          <label className="span-2">
            URL 또는 핸들
            <input
              value={form.handleOrUrl ?? ""}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setForm((current) => ({ ...current, handleOrUrl: value }));
              }}
              placeholder="예: https://www.youtube.com/channel/..., @account"
            />
          </label>

          <label>
            집중도
            <input
              min={0}
              max={5}
              type="number"
              value={form.focusLevel}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setForm((current) => ({
                  ...current,
                  focusLevel: Number(value),
                }));
              }}
            />
          </label>

          <label className="checkbox-row">
            <input
              checked={form.isEnabled}
              type="checkbox"
              onChange={(event) => {
                const checked = event.currentTarget.checked;
                setForm((current) => ({ ...current, isEnabled: checked }));
              }}
            />
            활성화
          </label>

          <label className="span-2">
            메모
            <textarea
              rows={3}
              value={form.notes ?? ""}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setForm((current) => ({ ...current, notes: value }));
              }}
              placeholder="이 소스를 왜 추적하는지 간단히 남길 수 있습니다."
            />
          </label>

          <div className="action-row span-2">
            <button className="primary-button icon-button" disabled={busy} type="submit">
              <LuPlus />
              <span>{form.id ? "수정 저장" : "새 소스 추가"}</span>
            </button>
            <button
              className="ghost-button"
              disabled={busy}
              type="button"
              onClick={() => setForm(emptyForm)}
            >
              폼 초기화
            </button>
            <button
              className="ghost-button icon-button"
              disabled={busy}
              type="button"
              onClick={handlePreviewImport}
            >
              <LuImport />
              <span>기존 config 미리보기</span>
            </button>
            <button className="ghost-button icon-button" disabled={busy} type="button" onClick={handleImport}>
              <LuImport />
              <span>기존 config 가져오기</span>
            </button>
          </div>
        </form>
      </div>

      {importPreview ? (
        <div className="panel muted-panel">
          <div className="panel-header compact">
            <div>
              <p className="eyebrow">Import</p>
              <h3>feed_config Preview</h3>
            </div>
            <p className="stats-copy">
              총 {importPreview.total}개, 신규 {importPreview.newCount}개, 중복 {importPreview.skippedCount}개
            </p>
          </div>
          <div className="pill-grid">
            {importPreview.items.slice(0, 8).map((item) => (
              <div className="pill-card" key={item.canonicalId}>
                <strong>{item.displayName}</strong>
                <span>{item.canonicalId}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Inventory</p>
            <h3 className="panel-title">
              <LuRows3 />
              <span>Current Sources</span>
            </h3>
          </div>
          <p className="stats-copy">
            {sources.length}개 등록됨 · 현재 {previewSources.length}개 미리보기
          </p>
        </div>

        <div className="source-preview-grid">
          {previewSources.map((source) => (
            <article className="source-card" key={source.id}>
              <div className="source-card-top">
                <span className="source-platform">{source.platform}</span>
                <span className={`source-status ${source.isEnabled ? "enabled" : "disabled"}`}>
                  {source.isEnabled ? (
                    <>
                      <LuBadgeCheck />
                      <span>활성</span>
                    </>
                  ) : (
                    <>
                      <LuCircleOff />
                      <span>비활성</span>
                    </>
                  )}
                </span>
              </div>
              <strong>{source.displayName}</strong>
              <span className="row-subcopy">{source.handleOrUrl ?? source.canonicalId}</span>
              <div className="source-meta">
                <span>ID: {source.canonicalId}</span>
                <span>집중도 {source.focusLevel}</span>
              </div>
              <div className="row-actions left">
                <button
                  className="text-button"
                  type="button"
                  onClick={() =>
                    setForm({
                      id: source.id,
                      platform: source.platform,
                      sourceType: source.sourceType,
                      displayName: source.displayName,
                      canonicalId: source.canonicalId,
                      handleOrUrl: source.handleOrUrl,
                      isEnabled: source.isEnabled,
                      focusLevel: source.focusLevel,
                      notes: source.notes,
                    })
                  }
                >
                  편집
                </button>
                <button className="text-button danger inline-icon" type="button" onClick={() => handleDelete(source.id)}>
                  <LuTrash2 />
                  <span>삭제</span>
                </button>
              </div>
            </article>
          ))}
        </div>

        {sources.length === 0 ? <div className="empty-state">아직 등록된 수집 대상이 없습니다.</div> : null}

        {sources.length > previewSources.length ? (
          <div className="more-row">
            <button
              className="ghost-button icon-button"
              type="button"
              onClick={() => setVisibleCount((current) => Math.min(current + 6, sources.length))}
            >
              <LuRows3 />
              <span>더보기</span>
            </button>
          </div>
        ) : null}

        {sources.length > 6 && previewSources.length >= sources.length ? (
          <div className="more-row">
            <button className="ghost-button" type="button" onClick={() => setVisibleCount(6)}>
              처음 6개만 보기
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
