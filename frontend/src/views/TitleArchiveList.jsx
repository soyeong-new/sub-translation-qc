// 등록된 작품(타이틀명 아카이브) 목록 — 이어서 검토하거나(열기), 다시
// 분석하거나(새로고침), 지울 수 있다(삭제). TitleListView의 등록 폼 아래에
// 렌더링된다.

import { useEffect, useRef, useState } from "react";
import { listTitles, deleteTitle, rerunAnalysis, pollTargetVersionStatus, getStorageUsage } from "../api.js";

function formatGB(bytes) {
  return (bytes / 1024 ** 3).toFixed(1);
}

const STATUS_LABELS = {
  analyzing: "분석 중...",
  awaiting_confirmation: "성별/격식 확인 필요",
  verifying: "AI 검증 중...",
  review: "검토 가능",
  failed: "실패",
};

const STATUS_DOT_CLASS = {
  analyzing: "bg-muted-foreground/50",
  awaiting_confirmation: "bg-warning",
  verifying: "bg-muted-foreground/50",
  review: "bg-success",
  failed: "bg-destructive",
};

// 카드마다 상태 텍스트를 반복하는 대신, 색의 의미를 한 번만 설명하는 범례.
const STATUS_LEGEND = [
  { key: "review", dot: "bg-success" },
  { key: "awaiting_confirmation", dot: "bg-warning" },
  { key: "analyzing", dot: "bg-muted-foreground/50" },
  { key: "failed", dot: "bg-destructive" },
];

const smallBtnBase =
  "inline-flex items-center justify-center rounded-lg border px-2.5 py-1 text-xs font-medium " +
  "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
  "disabled:cursor-not-allowed disabled:opacity-50";
const openBtnClass = `${smallBtnBase} border-primary/40 bg-primary/10 text-primary hover:bg-primary/20`;
const rerunBtnClass = `${smallBtnBase} border-input bg-background text-foreground hover:bg-accent`;
const deleteBtnClass =
  "inline-flex h-6 w-6 items-center justify-center text-destructive/70 leading-none " +
  "transition-colors hover:text-destructive focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

export default function TitleArchiveList({ onOpen }) {
  const [titles, setTitles] = useState(null); // null = 로딩 중
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null); // 지금 처리 중인 target_version/title id
  const [storage, setStorage] = useState(null); // { used, total } bytes

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

  function refreshStorage() {
    getStorageUsage()
      .then((data) => {
        if (isMountedRef.current) setStorage(data);
      })
      .catch(() => {}); // 저장공간 바는 부가 정보라 실패해도 조용히 무시
  }

  useEffect(refresh, []);
  useEffect(refreshStorage, []);

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
      refreshStorage();
    } catch (err) {
      setError(err.message ?? "삭제 중 오류가 발생했습니다.");
    } finally {
      if (isMountedRef.current) setBusyId(null);
    }
  }

  if (titles === null || titles.length === 0) return null;

  return (
    <div className="flex min-w-0 max-w-2xl flex-1 flex-col gap-4">
      {storage && (
        <div className="rounded-2xl border border-border/50 bg-muted/30 p-5 backdrop-blur-md">
          <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
            <span>Storage</span>
            <span>
              {formatGB(storage.used)}GB / {formatGB(storage.total)}GB 사용 중
            </span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
            <div
              className={`h-full rounded-full ${
                storage.used / storage.total > 0.9 ? "bg-destructive" : "bg-primary"
              }`}
              style={{ width: `${Math.min(100, (storage.used / storage.total) * 100)}%` }}
            />
          </div>
        </div>
      )}
      <div className="rounded-2xl border border-border/50 bg-muted/30 p-6 backdrop-blur-md">
        <h2 className="mb-2 text-2xl font-semibold text-foreground">Archive</h2>
        <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          {STATUS_LEGEND.map(({ key, dot }) => (
            <span key={key} className="inline-flex items-center gap-1.5">
              <span className={`h-2 w-2 rounded-full ${dot}`} />
              {STATUS_LABELS[key]}
            </span>
          ))}
        </div>
        {error && (
          <p role="status" aria-live="polite" className="mb-2 text-sm text-destructive">{error}</p>
        )}
        <ul className="space-y-4">
        {titles.map((title) => {
          const targetVersions = title.episodes.flatMap((ep) => ep.target_versions);
          return (
            <li
              key={title.id}
              className="rounded-2xl border border-border/50 bg-card/70 p-5 shadow-sm backdrop-blur-sm transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] hover:-translate-y-0.5 hover:shadow-md"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="flex items-center gap-1.5">
                    {targetVersions.map((tv) => (
                      <span
                        key={tv.id}
                        role="img"
                        aria-label={STATUS_LABELS[tv.status] || tv.status}
                        title={STATUS_LABELS[tv.status] || tv.status}
                        className={`h-2.5 w-2.5 rounded-full ${
                          STATUS_DOT_CLASS[tv.status] || "bg-muted-foreground/50"
                        }`}
                      />
                    ))}
                  </div>
                  <span className="text-sm font-medium text-foreground">{title.name}</span>
                  {targetVersions.map((tv) => (
                    <div key={tv.id} className="flex items-center gap-2">
                      <button disabled={busyId === tv.id} onClick={() => handleOpen(tv)} className={openBtnClass}>
                        열기
                      </button>
                      <button disabled={busyId === tv.id} onClick={() => handleRerun(tv)} className={rerunBtnClass}>
                        재분석
                      </button>
                    </div>
                  ))}
                </div>
                <button
                  disabled={busyId === title.id}
                  onClick={() => handleDelete(title)}
                  aria-label="삭제"
                  className={deleteBtnClass}
                >
                  ×
                </button>
              </div>
              <ul className="mt-2 space-y-1.5">
                {targetVersions.length === 0 && (
                  <li className="text-xs text-muted-foreground">분석 없음</li>
                )}
                {targetVersions.map((tv) => (
                  <li key={tv.id} className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded-lg bg-muted/50 px-2.5 py-1 text-muted-foreground">
                      {tv.display_name}
                    </span>
                    {tv.reviewers.length > 0 && (
                      <span className="rounded-lg bg-muted/50 px-2.5 py-1 text-muted-foreground">
                        {tv.reviewers.join(", ")}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </li>
          );
        })}
        </ul>
      </div>
    </div>
  );
}
