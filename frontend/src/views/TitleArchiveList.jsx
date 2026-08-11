// 등록된 작품(타이틀명 아카이브) 목록 — 이어서 검토하거나(열기), 다시
// 분석하거나(새로고침), 지울 수 있다(삭제). TitleListView의 등록 폼 아래에
// 렌더링된다.

import { useEffect, useRef, useState } from "react";
import { listTitles, deleteTitle, rerunAnalysis, pollTargetVersionStatus } from "../api.js";

const STATUS_LABELS = {
  analyzing: "분석 중...",
  awaiting_confirmation: "성별/격식 확인 필요",
  verifying: "AI 검증 중...",
  review: "검토 가능",
  failed: "실패",
};

const STATUS_BADGE_CLASS = {
  analyzing: "bg-muted text-muted-foreground border-border",
  awaiting_confirmation: "bg-warning/10 text-warning border-warning/30",
  verifying: "bg-muted text-muted-foreground border-border",
  review: "bg-success/10 text-success border-success/30",
  failed: "bg-destructive/10 text-destructive border-destructive/30",
};

const smallBtnBase =
  "inline-flex items-center justify-center rounded-md border px-2.5 py-1 text-xs font-medium " +
  "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
  "disabled:cursor-not-allowed disabled:opacity-50";
const openBtnClass = `${smallBtnBase} border-primary/40 bg-primary/10 text-primary hover:bg-primary/20`;
const rerunBtnClass = `${smallBtnBase} border-input bg-background text-foreground hover:bg-accent`;
const deleteBtnClass = `${smallBtnBase} border-destructive/40 bg-destructive/10 text-destructive hover:bg-destructive/20`;

export default function TitleArchiveList({ onOpen }) {
  const [titles, setTitles] = useState(null); // null = 로딩 중
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null); // 지금 처리 중인 target_version/title id

  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  function refresh() {
    listTitles()
      .then((data) => {
        if (isMountedRef.current) setTitles(data);
      })
      .catch((err) => {
        if (isMountedRef.current) setError(err.message ?? "목록을 불러오지 못했습니다.");
      });
  }

  useEffect(refresh, []);

  async function waitThenOpen(targetVersionId) {
    setBusyId(targetVersionId);
    setError(null);
    try {
      const status = await pollTargetVersionStatus(targetVersionId, {
        isMounted: () => isMountedRef.current,
      });
      onOpen(targetVersionId, status);
    } catch (err) {
      setError(err.message ?? "요청 중 오류가 발생했습니다.");
    } finally {
      if (isMountedRef.current) setBusyId(null);
    }
  }

  function handleOpen(tv) {
    if (tv.status === "review" || tv.status === "awaiting_confirmation") {
      onOpen(tv.id, tv.status);
      return;
    }
    waitThenOpen(tv.id);
  }

  async function handleRerun(tv) {
    setBusyId(tv.id);
    setError(null);
    try {
      await rerunAnalysis(tv.id);
    } catch (err) {
      if (isMountedRef.current) {
        setError(err.message ?? "재분석 요청 중 오류가 발생했습니다.");
        setBusyId(null);
      }
      return;
    }
    await waitThenOpen(tv.id);
  }

  async function handleDelete(title) {
    if (!window.confirm(`"${title.name}"을(를) 삭제할까요? 되돌릴 수 없습니다.`)) return;
    setBusyId(title.id);
    setError(null);
    try {
      await deleteTitle(title.id);
      refresh();
    } catch (err) {
      setError(err.message ?? "삭제 중 오류가 발생했습니다.");
    } finally {
      if (isMountedRef.current) setBusyId(null);
    }
  }

  if (titles === null || titles.length === 0) return null;

  return (
    <div className="mt-10 w-full max-w-2xl">
      <h2 className="mb-3 text-sm font-semibold text-foreground">등록된 작품</h2>
      {error && (
        <p role="status" aria-live="polite" className="mb-2 text-sm text-destructive">{error}</p>
      )}
      <ul className="space-y-3">
        {titles.map((title) => {
          const targetVersions = title.episodes.flatMap((ep) => ep.target_versions);
          return (
            <li key={title.id} className="rounded-lg border border-border bg-card p-4 shadow-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-foreground">{title.name}</span>
                <button
                  disabled={busyId === title.id}
                  onClick={() => handleDelete(title)}
                  className={deleteBtnClass}
                >
                  삭제
                </button>
              </div>
              <ul className="mt-2 space-y-1.5">
                {targetVersions.length === 0 && (
                  <li className="text-xs text-muted-foreground">분석 없음</li>
                )}
                {targetVersions.map((tv) => (
                  <li key={tv.id} className="flex flex-wrap items-center gap-2 text-xs">
                    <span
                      className={`inline-flex items-center rounded-full border px-2 py-0.5 ${
                        STATUS_BADGE_CLASS[tv.status] || "bg-muted text-muted-foreground border-border"
                      }`}
                    >
                      {STATUS_LABELS[tv.status] || tv.status}
                    </span>
                    <span className="text-muted-foreground">
                      {tv.target_language}({tv.variant})
                    </span>
                    <button disabled={busyId === tv.id} onClick={() => handleOpen(tv)} className={openBtnClass}>
                      열기
                    </button>
                    <button disabled={busyId === tv.id} onClick={() => handleRerun(tv)} className={rerunBtnClass}>
                      새로고침
                    </button>
                  </li>
                ))}
              </ul>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
