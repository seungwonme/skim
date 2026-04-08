import { useState } from "react";
import { LuBadgeCheck, LuKeyRound, LuRefreshCw, LuTrash2 } from "react-icons/lu";

import { deleteCredential, saveCredential, startLogin, verifySession } from "../lib/api";
import type { CredentialInput, PlatformCredential } from "../lib/types";

interface CredentialsPanelProps {
  credentials: PlatformCredential[];
  onChanged: () => Promise<void>;
  report: (message: string, tone?: "neutral" | "success" | "error") => void;
}

const emptyCredential: CredentialInput = {
  platform: "threads",
  accountLabel: "",
  loginIdentifier: "",
  password: "",
};

export function CredentialsPanel({ credentials, onChanged, report }: CredentialsPanelProps) {
  const [form, setForm] = useState<CredentialInput>(emptyCredential);
  const [busy, setBusy] = useState(false);

  async function handleSave(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      await saveCredential(form);
      report("자격 증명을 저장했습니다. 비밀번호는 macOS Keychain에만 보관됩니다.", "success");
      setForm(emptyCredential);
      await onChanged();
    } catch (error) {
      report(`자격 증명 저장 실패: ${String(error)}`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(id: number) {
    if (!window.confirm("이 자격 증명을 삭제할까요?")) {
      return;
    }

    setBusy(true);
    try {
      await deleteCredential(id);
      report("자격 증명을 삭제했습니다.", "success");
      await onChanged();
    } catch (error) {
      report(`자격 증명 삭제 실패: ${String(error)}`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function handleVerify(credential: PlatformCredential) {
    setBusy(true);
    try {
      const status = await verifySession(credential.platform, credential.sessionPath);
      report(
        `${credential.platform} 세션 상태: ${status.status} (${status.cookieCount} cookies)`,
        status.status === "healthy" ? "success" : "neutral",
      );
      await onChanged();
    } catch (error) {
      report(`세션 상태 확인 실패: ${String(error)}`, "error");
    } finally {
      setBusy(false);
    }
  }

  async function handleLogin(credential: PlatformCredential) {
    setBusy(true);
    try {
      const message = await startLogin(credential.id);
      report(message, "success");
    } catch (error) {
      report(`로그인 실행 실패: ${String(error)}`, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel-stack">
      <div className="panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">Security</p>
            <h2 className="panel-title">
              <LuKeyRound />
              <span>Platform Credentials</span>
            </h2>
          </div>
          <p className="panel-subcopy">
            로그인 식별자는 DB에 저장하고 비밀번호는 macOS Keychain에만 저장합니다.
          </p>
        </div>

        <form className="form-grid" onSubmit={handleSave}>
          <label>
            플랫폼
            <select
              value={form.platform}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setForm((current) => ({ ...current, platform: value }));
              }}
            >
              <option value="threads">Threads</option>
              <option value="x">X</option>
              <option value="linkedin">LinkedIn</option>
              <option value="reddit">Reddit</option>
            </select>
          </label>

          <label>
            계정 레이블
            <input
              required
              value={form.accountLabel}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setForm((current) => ({ ...current, accountLabel: value }));
              }}
              placeholder="예: personal, work"
            />
          </label>

          <label>
            로그인 식별자
            <input
              required
              value={form.loginIdentifier}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setForm((current) => ({ ...current, loginIdentifier: value }));
              }}
              placeholder="이메일 또는 사용자명"
            />
          </label>

          <label>
            비밀번호
            <input
              required
              type="password"
              value={form.password}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setForm((current) => ({ ...current, password: value }));
              }}
              placeholder="Keychain에만 저장됩니다"
            />
          </label>

          <div className="action-row span-2">
            <button className="primary-button icon-button" disabled={busy} type="submit">
              <LuKeyRound />
              <span>자격 증명 저장</span>
            </button>
            <button
              className="ghost-button"
              disabled={busy}
              type="button"
              onClick={() => setForm(emptyCredential)}
            >
              폼 초기화
            </button>
          </div>
        </form>
      </div>

      <div className="panel">
        <div className="panel-header compact">
          <div>
            <p className="eyebrow">Sessions</p>
            <h3>Stored Accounts</h3>
          </div>
          <p className="stats-copy">{credentials.length}개 계정 저장됨</p>
        </div>

        <div className="data-table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>플랫폼</th>
                <th>계정</th>
                <th>식별자</th>
                <th>세션</th>
                <th>마지막 확인</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {credentials.map((credential) => (
                <tr key={credential.id}>
                  <td>{credential.platform}</td>
                  <td>{credential.accountLabel}</td>
                  <td>{credential.loginIdentifier}</td>
                  <td>{credential.sessionStatus}</td>
                  <td>{credential.lastVerifiedAt ?? "-"}</td>
                  <td className="row-actions">
                    <button className="text-button inline-icon" type="button" onClick={() => handleVerify(credential)}>
                      <LuBadgeCheck />
                      <span>확인</span>
                    </button>
                    <button className="text-button inline-icon" type="button" onClick={() => handleLogin(credential)}>
                      <LuRefreshCw />
                      <span>브라우저 로그인</span>
                    </button>
                    <button className="text-button danger inline-icon" type="button" onClick={() => handleDelete(credential.id)}>
                      <LuTrash2 />
                      <span>삭제</span>
                    </button>
                  </td>
                </tr>
              ))}
              {credentials.length === 0 ? (
                <tr>
                  <td className="empty-row" colSpan={6}>
                    아직 저장된 자격 증명이 없습니다.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
