// 등록된 작품(타이틀명 아카이브) 목록 — 이어서 검토하거나(열기), 다시
// 분석하거나(새로고침), 지울 수 있다(삭제). 회차별로 새 언어 버전을 추가할
// 수도 있다(같은 title_id 아래 묶여야 성별 재사용 힌트가 연결된다).
// TitleListView의 등록 폼 아래에 렌더링된다.

import { useEffect, useRef, useState } from "react";
import {
  listTitles, deleteTitle, deleteTargetVersion, rerunAnalysis, pollTargetVersionStatus, getStorageUsage,
  listLanguageProfiles, uploadSrt, uploadSrtKo, uploadVideo, createEpisode,
  createTargetVersion, runAnalysis, updateTitleType, updateCharacterGender,
} from "../api.js";
import FileDropzone from "../components/FileDropzone.jsx";

const SRT_EXTENSIONS = [".srt"];
const VIDEO_EXTENSIONS = [".mp4", ".mov", ".mkv", ".avi"];

function getExtension(filename) {
  const idx = filename.lastIndexOf(".");
  return idx === -1 ? "" : filename.slice(idx).toLowerCase();
}

// <select>의 value/React key로만 쓰이는 안정적인 문자열 (TitleListView.jsx와 동일 패턴).
function profileKey(p) {
  return `${p.language}_${p.variant}`;
}

function formatGB(bytes) {
  return (bytes / 1024 ** 3).toFixed(1);
}

// 펼쳐놓은 title 카드 id들 — 새로고침해도 유지되도록 App.jsx의 qc_screen과
// 같은 패턴으로 localStorage에 저장한다.
const EXPANDED_STORAGE_KEY = "qc_archive_expanded";

function loadExpandedIds() {
  try {
    const raw = localStorage.getItem(EXPANDED_STORAGE_KEY);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch {
    return new Set();
  }
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

const TYPE_TABS = [
  { key: "all", label: "전체" },
  { key: "movie", label: "영화" },
  { key: "series", label: "드라마" },
];

const smallBtnBase =
  "inline-flex items-center justify-center rounded-lg border px-2.5 py-1 text-xs font-medium " +
  "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
  "disabled:cursor-not-allowed disabled:opacity-50";
const openBtnClass = `${smallBtnBase} border-primary/40 bg-primary/10 text-primary hover:bg-primary/20`;
const rerunBtnClass = `${smallBtnBase} border-input bg-background text-foreground hover:bg-accent`;
const addLangBtnClass = `${smallBtnBase} border-dashed border-input text-muted-foreground hover:bg-accent hover:text-foreground`;
const deleteBtnClass =
  "inline-flex h-6 w-6 items-center justify-center text-destructive/70 leading-none " +
  "transition-colors hover:text-destructive focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";
const inputClass =
  "block w-full rounded-lg border border-input bg-background px-2.5 py-1.5 text-xs text-foreground " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

function AddLanguageForm({ availableProfiles, selectedProfile, onSelectProfile,
                            srtFile, onSrtSelected, progress, status, onSubmit, onCancel }) {
  const isSubmitting = status?.kind === "loading";
  const canSubmit = Boolean(selectedProfile && srtFile) && !isSubmitting;

  return (
    <div className="mt-2 space-y-2 rounded-xl border border-dashed border-border/70 bg-background/50 p-3">
      <div className="flex items-end gap-2">
        <div className="min-w-0 flex-1">
          <label htmlFor="add-language-select" className="mb-1.5 block whitespace-nowrap text-sm font-medium text-foreground">
            언어
          </label>
          <select
            id="add-language-select"
            value={selectedProfile ? profileKey(selectedProfile) : ""}
            onChange={(e) => {
              const match = availableProfiles.find((p) => profileKey(p) === e.target.value);
              onSelectProfile(match ?? null);
            }}
            disabled={isSubmitting}
            className={inputClass}
          >
            <option value="">언어 선택...</option>
            {availableProfiles.map((p) => (
              <option key={profileKey(p)} value={profileKey(p)}>{p.display_name}</option>
            ))}
          </select>
        </div>
        <div className="min-w-0 flex-1">
          <FileDropzone
            id="add-language-srt"
            label="대상언어 SRT 자막"
            accept={SRT_EXTENSIONS.join(",")}
            file={srtFile}
            onFileSelected={onSrtSelected}
            progress={progress}
            disabled={isSubmitting}
          />
        </div>
        <button type="button" onClick={onCancel} disabled={isSubmitting} className={`shrink-0 ${rerunBtnClass}`}>
          취소
        </button>
      </div>
      {status && (
        <p role="status" aria-live="polite"
           className={`text-xs ${status.kind === "error" ? "text-destructive" : "text-muted-foreground"}`}>
          {status.message}
        </p>
      )}
      <button type="button" onClick={onSubmit} disabled={!canSubmit} className={openBtnClass}>
        추가 및 분석 시작
      </button>
    </div>
  );
}

// 기존 title 밑에 새 회차(episode)를 추가한다 — 언어 추가와 달리 title
// 레벨 액션이라(특정 episode에 속하지 않음) 자체 상태를 갖는 독립 폼으로
// 만든다. 새 영상+대상언어 SRT를 업로드해 분석까지 바로 시작한다.
function AddEpisodeForm({ titleId, languageProfiles, isMountedRef, onDone, onCancel }) {
  const [episodeNo, setEpisodeNo] = useState("");
  const [videoFile, setVideoFile] = useState(null);
  const [koreanSrtFile, setKoreanSrtFile] = useState(null);
  const [selectedProfile, setSelectedProfile] = useState(null);
  const [srtFile, setSrtFile] = useState(null);
  const [videoProgress, setVideoProgress] = useState(null);
  const [koreanSrtProgress, setKoreanSrtProgress] = useState(null);
  const [srtProgress, setSrtProgress] = useState(null);
  const [status, setStatus] = useState(null);

  const isSubmitting = status?.kind === "loading";
  const canSubmit = Boolean(videoFile && selectedProfile && srtFile) && !isSubmitting;

  function handleVideoSelected(selected) {
    if (!VIDEO_EXTENSIONS.includes(getExtension(selected.name))) {
      setStatus({
        kind: "error",
        message: `지원하지 않는 영상 파일 형식입니다. (허용: ${VIDEO_EXTENSIONS.join(", ")})`,
      });
      return;
    }
    setStatus(null);
    setVideoFile(selected);
  }

  function makeSrtHandler(setter) {
    return (selected) => {
      if (!SRT_EXTENSIONS.includes(getExtension(selected.name))) {
        setStatus({
          kind: "error",
          message: `지원하지 않는 자막 파일 형식입니다. (허용: ${SRT_EXTENSIONS.join(", ")})`,
        });
        return;
      }
      setStatus(null);
      setter(selected);
    };
  }

  async function handleSubmit() {
    if (!canSubmit) return;
    setStatus({ kind: "loading", message: "업로드 중..." });
    try {
      const videoUpload = await uploadVideo(videoFile, setVideoProgress);
      let koreanSrtPath = null;
      if (koreanSrtFile) {
        setStatus({ kind: "loading", message: "한국어 SRT 업로드 중..." });
        const koreanSrtUpload = await uploadSrtKo(koreanSrtFile, setKoreanSrtProgress);
        koreanSrtPath = koreanSrtUpload.path;
      }
      const episodeNoValue = episodeNo.trim() ? Number(episodeNo) : null;
      const episode = await createEpisode(titleId, episodeNoValue, videoUpload.path, koreanSrtPath);
      setStatus({ kind: "loading", message: "대상언어 SRT 업로드 중..." });
      const srtUpload = await uploadSrt(srtFile, setSrtProgress);
      const tv = await createTargetVersion(episode.id, selectedProfile.language, selectedProfile.variant);
      setStatus({ kind: "loading", message: "분석 중..." });
      await runAnalysis(tv.id, srtUpload.path);
      const doneStatus = await pollTargetVersionStatus(tv.id, { isMounted: () => isMountedRef.current });
      if (!isMountedRef.current) return;
      onDone(tv.id, doneStatus);
    } catch (err) {
      if (isMountedRef.current) {
        setStatus({ kind: "error", message: err.message ?? "회차 추가 중 오류가 발생했습니다." });
      }
    } finally {
      if (isMountedRef.current) {
        setVideoProgress(null);
        setKoreanSrtProgress(null);
        setSrtProgress(null);
      }
    }
  }

  return (
    <div className="mt-3 space-y-2 rounded-xl border border-dashed border-border/70 bg-background/50 p-3">
      <div className="flex items-end gap-2">
        <div className="w-20 shrink-0">
          <label htmlFor="add-episode-no" className="mb-1.5 block whitespace-nowrap text-sm font-medium text-foreground">
            회차
          </label>
          <input
            id="add-episode-no"
            type="number"
            value={episodeNo}
            onChange={(e) => setEpisodeNo(e.target.value)}
            disabled={isSubmitting}
            placeholder="예: 2"
            className={inputClass}
          />
        </div>
        <div className="min-w-0 flex-1">
          <label htmlFor="add-episode-lang" className="mb-1.5 block whitespace-nowrap text-sm font-medium text-foreground">
            언어
          </label>
          <select
            id="add-episode-lang"
            value={selectedProfile ? profileKey(selectedProfile) : ""}
            onChange={(e) => {
              const match = languageProfiles.find((p) => profileKey(p) === e.target.value);
              setSelectedProfile(match ?? null);
            }}
            disabled={isSubmitting}
            className={inputClass}
          >
            <option value="">언어 선택...</option>
            {languageProfiles.map((p) => (
              <option key={profileKey(p)} value={profileKey(p)}>{p.display_name}</option>
            ))}
          </select>
        </div>
        <button type="button" onClick={onCancel} disabled={isSubmitting} className={`shrink-0 ${rerunBtnClass}`}>
          취소
        </button>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <FileDropzone
          id="add-episode-video"
          label="한국어 원본 영상"
          accept={VIDEO_EXTENSIONS.join(",")}
          file={videoFile}
          onFileSelected={handleVideoSelected}
          progress={videoProgress}
          disabled={isSubmitting}
        />
        <FileDropzone
          id="add-episode-target-srt"
          label="대상언어 SRT 자막"
          accept={SRT_EXTENSIONS.join(",")}
          file={srtFile}
          onFileSelected={makeSrtHandler(setSrtFile)}
          progress={srtProgress}
          disabled={isSubmitting}
        />
        <FileDropzone
          id="add-episode-korean-srt"
          label="한국어 SRT (선택)"
          accept={SRT_EXTENSIONS.join(",")}
          file={koreanSrtFile}
          onFileSelected={makeSrtHandler(setKoreanSrtFile)}
          progress={koreanSrtProgress}
          disabled={isSubmitting}
        />
      </div>
      {status && (
        <p role="status" aria-live="polite"
           className={`text-xs ${status.kind === "error" ? "text-destructive" : "text-muted-foreground"}`}>
          {status.message}
        </p>
      )}
      <button type="button" onClick={handleSubmit} disabled={!canSubmit} className={openBtnClass}>
        회차 추가 및 분석 시작
      </button>
    </div>
  );
}

export default function TitleArchiveList({ onOpen }) {
  const [titles, setTitles] = useState(null); // null = 로딩 중
  const [filterType, setFilterType] = useState("all"); // "all" | "movie" | "series"
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null); // 지금 처리 중인 target_version/title id
  const [storage, setStorage] = useState(null); // { used, total } bytes
  const [languageProfiles, setLanguageProfiles] = useState([]);

  // "회차 추가" 폼은 한 번에 하나의 title에서만 연다.
  const [addEpisodeTitleId, setAddEpisodeTitleId] = useState(null);

  // "언어 추가" 폼은 한 번에 하나의 episode에서만 연다.
  const [addLanguageEpisodeId, setAddLanguageEpisodeId] = useState(null);
  const [addLanguageProfile, setAddLanguageProfile] = useState(null);
  const [addLanguageSrtFile, setAddLanguageSrtFile] = useState(null);
  const [addLanguageProgress, setAddLanguageProgress] = useState(null);
  const [addLanguageStatus, setAddLanguageStatus] = useState(null);

  const [expandedIds, setExpandedIds] = useState(loadExpandedIds);
  const [confirmState, setConfirmState] = useState(null); // { message, onConfirm } | null

  function toggleExpanded(titleId, isOpen) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (isOpen) next.add(titleId);
      else next.delete(titleId);
      localStorage.setItem(EXPANDED_STORAGE_KEY, JSON.stringify([...next]));
      return next;
    });
  }

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
  useEffect(() => {
    listLanguageProfiles()
      .then((profiles) => {
        if (isMountedRef.current) setLanguageProfiles(profiles);
      })
      .catch(() => {}); // 실패해도 "언어 추가" 폼만 못 쓰게 될 뿐이라 조용히 무시
  }, []);

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

  async function handleChangeType(title, newType) {
    if (newType === title.type) return;
    setBusyId(title.id);
    setError(null);
    try {
      await updateTitleType(title.id, newType);
      refresh();
    } catch (err) {
      setError(err.message ?? "유형 변경 중 오류가 발생했습니다.");
    } finally {
      if (isMountedRef.current) setBusyId(null);
    }
  }

  // 확인 화면에서 잘못 체크된 캐릭터 성별을 여기서 바로 고친다(design
  // 2026-08-31) — 다인물이 섞인 줄의 referent에 이름이 잘못 붙는 등으로
  // title 단위 fact가 틀리게 저장되는 사고가 실측으로 확인됐다.
  async function handleChangeCharacterGender(fact, newGender) {
    if (newGender === fact.gender) return;
    setBusyId(fact.id);
    setError(null);
    try {
      await updateCharacterGender(fact.id, newGender);
      refresh();
    } catch (err) {
      setError(err.message ?? "캐릭터 성별 변경 중 오류가 발생했습니다.");
    } finally {
      if (isMountedRef.current) setBusyId(null);
    }
  }

  function handleDelete(title) {
    setConfirmState({
      message: `"${title.name}"을(를) 삭제할까요? 되돌릴 수 없습니다.`,
      onConfirm: () => runDelete(title),
    });
  }

  async function runDelete(title) {
    setConfirmState(null);
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

  function handleDeleteVersion(tv) {
    setConfirmState({
      message: `"${tv.display_name}"을(를) 삭제할까요? 되돌릴 수 없습니다.`,
      onConfirm: () => runDeleteVersion(tv),
    });
  }

  async function runDeleteVersion(tv) {
    setConfirmState(null);
    setBusyId(tv.id);
    setError(null);
    try {
      await deleteTargetVersion(tv.id);
      refresh();
    } catch (err) {
      setError(err.message ?? "삭제 중 오류가 발생했습니다.");
    } finally {
      if (isMountedRef.current) setBusyId(null);
    }
  }

  function openAddLanguage(episodeId) {
    setAddLanguageEpisodeId(episodeId);
    setAddLanguageProfile(null);
    setAddLanguageSrtFile(null);
    setAddLanguageStatus(null);
  }

  function closeAddLanguage() {
    setAddLanguageEpisodeId(null);
  }

  function handleAddLanguageSrtSelected(selected) {
    if (!SRT_EXTENSIONS.includes(getExtension(selected.name))) {
      setAddLanguageStatus({
        kind: "error",
        message: `지원하지 않는 자막 파일 형식입니다. (허용: ${SRT_EXTENSIONS.join(", ")})`,
      });
      return;
    }
    setAddLanguageStatus(null);
    setAddLanguageSrtFile(selected);
  }

  async function handleAddLanguageSubmit(episodeId) {
    if (!addLanguageProfile || !addLanguageSrtFile) return;
    setAddLanguageProgress(0);
    setAddLanguageStatus({ kind: "loading", message: "업로드 중..." });
    try {
      const srtUpload = await uploadSrt(addLanguageSrtFile, setAddLanguageProgress);
      const tv = await createTargetVersion(episodeId, addLanguageProfile.language, addLanguageProfile.variant);
      setAddLanguageStatus({ kind: "loading", message: "분석 중..." });
      await runAnalysis(tv.id, srtUpload.path);
      const doneStatus = await pollTargetVersionStatus(tv.id, { isMounted: () => isMountedRef.current });
      if (!isMountedRef.current) return;
      closeAddLanguage();
      refresh();
      onOpen(tv.id, doneStatus);
    } catch (err) {
      if (isMountedRef.current) {
        setAddLanguageStatus({ kind: "error", message: err.message ?? "언어 추가 중 오류가 발생했습니다." });
      }
    } finally {
      if (isMountedRef.current) setAddLanguageProgress(null);
    }
  }

  if (titles === null || titles.length === 0) return null;

  return (
    <div className="flex min-w-0 w-[calc(50%-1rem)] max-w-2xl flex-col gap-4">
      <div className="rounded-2xl border border-border/50 bg-muted/30 p-6 backdrop-blur-md">
        {storage && (
          <div className="mb-4 border-b border-border/40 pb-4">
            <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
              <span>Storage</span>
              <span>
                고정 {formatGB(storage.used - storage.media_used)}GB + 영상 {formatGB(storage.media_used)}GB
                {" "}/ 잔여 {formatGB(storage.total - storage.used)}GB
              </span>
            </div>
            <div className="flex h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-muted-foreground/40"
                style={{ width: `${Math.min(100, ((storage.used - storage.media_used) / storage.total) * 100)}%` }}
              />
              <div
                className={`h-full ${storage.used / storage.total > 0.9 ? "bg-destructive" : "bg-primary"}`}
                style={{ width: `${Math.min(100, (storage.media_used / storage.total) * 100)}%` }}
              />
            </div>
          </div>
        )}
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-2xl font-semibold text-foreground">Archive</h2>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            {STATUS_LEGEND.map(({ key, dot }) => (
              <span key={key} className="inline-flex items-center gap-1.5">
                <span className={`h-2 w-2 rounded-full ${dot}`} />
                {STATUS_LABELS[key]}
              </span>
            ))}
          </div>
        </div>
        <div className="mb-3 flex gap-1.5">
          {TYPE_TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setFilterType(tab.key)}
              className={
                filterType === tab.key
                  ? `${smallBtnBase} border-primary/40 bg-primary/10 text-primary`
                  : `${smallBtnBase} border-input bg-background text-muted-foreground hover:bg-accent`
              }
            >
              {tab.label}
            </button>
          ))}
        </div>
        {error && (
          <p role="status" aria-live="polite" className="mb-2 text-sm text-destructive">{error}</p>
        )}
        <ul className="space-y-4">
        {(filterType === "all" ? titles : titles.filter((t) => t.type === filterType)).map((title) => {
          const titleReviewers = [...new Set(
            title.episodes.flatMap((ep) => ep.target_versions.flatMap((tv) => tv.reviewers)),
          )];
          return (
          <li
            key={title.id}
            className="rounded-2xl border border-border/50 bg-card/70 p-5 shadow-sm backdrop-blur-sm transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] hover:-translate-y-0.5 hover:shadow-md"
          >
            <details
              className="group"
              open={expandedIds.has(title.id)}
              onToggle={(e) => toggleExpanded(title.id, e.target.open)}
            >
              <summary className="flex cursor-pointer list-none items-center justify-between gap-2 [&::-webkit-details-marker]:hidden">
                <div className="flex min-w-0 items-center gap-2">
                  <svg
                    viewBox="0 0 20 20"
                    fill="currentColor"
                    aria-hidden="true"
                    className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform group-open:rotate-90"
                  >
                    <path d="M7 5l6 5-6 5V5z" />
                  </svg>
                  <span className="truncate text-sm font-medium text-foreground">{title.name}</span>
                  <select
                    aria-label="유형"
                    value={title.type}
                    disabled={busyId === title.id}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => handleChangeType(title, e.target.value)}
                    className="shrink-0 rounded-lg border border-input bg-background px-2 py-0.5 text-xs text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <option value="movie">영화</option>
                    <option value="series">드라마</option>
                  </select>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {titleReviewers.length > 0 && (
                    <span className="text-xs text-muted-foreground">{titleReviewers.join(", ")}</span>
                  )}
                  <button
                    disabled={busyId === title.id}
                    onClick={(e) => { e.stopPropagation(); handleDelete(title); }}
                    aria-label="삭제"
                    className={deleteBtnClass}
                  >
                    ×
                  </button>
                </div>
              </summary>
              {title.character_genders?.length > 0 && (
                <details className="mt-3 border-t border-border/40 pt-3">
                  <summary className="cursor-pointer text-xs font-semibold text-muted-foreground">
                    캐릭터 성별 ({title.character_genders.length}명)
                  </summary>
                  <ul className="mt-1.5 space-y-1">
                    {title.character_genders.map((fact) => (
                      <li key={fact.id} className="flex items-center gap-2 text-xs">
                        <span className="text-foreground">{fact.character_name}</span>
                        <select
                          aria-label={`${fact.character_name} 성별`}
                          value={fact.gender}
                          disabled={busyId === fact.id}
                          onChange={(e) => handleChangeCharacterGender(fact, e.target.value)}
                          className="rounded-lg border border-input bg-background px-2 py-0.5 text-xs text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <option value="male">남성</option>
                          <option value="female">여성</option>
                        </select>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {title.episodes.map((ep) => {
              const usedKeys = new Set(ep.target_versions.map((tv) => `${tv.target_language}_${tv.variant}`));
              const availableProfiles = languageProfiles.filter((p) => !usedKeys.has(profileKey(p)));
              return (
                <div key={ep.id} className="mt-3 border-t border-border/40 pt-3 first:mt-2 first:border-t-0 first:pt-0">
                  {ep.episode_no != null && (
                    <div className="mb-1.5 text-xs font-semibold text-muted-foreground">{ep.episode_no}화</div>
                  )}
                  <ul className="space-y-1.5">
                    {ep.target_versions.length === 0 && (
                      <li className="text-xs text-muted-foreground">분석 없음</li>
                    )}
                    {ep.target_versions.map((tv) => (
                      <li key={tv.id} className="flex flex-wrap items-center gap-2 text-xs">
                        <span
                          role="img"
                          aria-label={STATUS_LABELS[tv.status] || tv.status}
                          title={STATUS_LABELS[tv.status] || tv.status}
                          className={`h-2.5 w-2.5 rounded-full ${
                            STATUS_DOT_CLASS[tv.status] || "bg-muted-foreground/50"
                          }`}
                        />
                        <span className="inline-block w-36 shrink-0 whitespace-nowrap rounded-lg bg-muted/50 px-2.5 py-1 text-muted-foreground">
                          {tv.display_name}
                        </span>
                        {tv.reviewers.length > 0 && (
                          <span className="rounded-lg bg-muted/50 px-2.5 py-1 text-muted-foreground">
                            {tv.reviewers.join(", ")}
                          </span>
                        )}
                        <button disabled={busyId === tv.id} onClick={() => handleOpen(tv)} className={openBtnClass}>
                          열기
                        </button>
                        <button disabled={busyId === tv.id} onClick={() => handleRerun(tv)} className={rerunBtnClass}>
                          재분석
                        </button>
                        <button
                          disabled={busyId === tv.id}
                          onClick={() => handleDeleteVersion(tv)}
                          aria-label="언어 삭제"
                          className={deleteBtnClass}
                        >
                          ×
                        </button>
                      </li>
                    ))}
                  </ul>
                  {addLanguageEpisodeId === ep.id ? (
                    <AddLanguageForm
                      availableProfiles={availableProfiles}
                      selectedProfile={addLanguageProfile}
                      onSelectProfile={setAddLanguageProfile}
                      srtFile={addLanguageSrtFile}
                      onSrtSelected={handleAddLanguageSrtSelected}
                      progress={addLanguageProgress}
                      status={addLanguageStatus}
                      onSubmit={() => handleAddLanguageSubmit(ep.id)}
                      onCancel={closeAddLanguage}
                    />
                  ) : (
                    availableProfiles.length > 0 && (
                      <button className={`mt-1.5 ${addLangBtnClass}`} onClick={() => openAddLanguage(ep.id)}>
                        + 언어 추가
                      </button>
                    )
                  )}
                </div>
              );
            })}
            {title.type === "series" && (addEpisodeTitleId === title.id ? (
              <AddEpisodeForm
                titleId={title.id}
                languageProfiles={languageProfiles}
                isMountedRef={isMountedRef}
                onDone={(tvId, status) => {
                  setAddEpisodeTitleId(null);
                  refresh();
                  onOpen(tvId, status);
                }}
                onCancel={() => setAddEpisodeTitleId(null)}
              />
            ) : (
              <button
                className={`mt-3 ${addLangBtnClass}`}
                onClick={() => setAddEpisodeTitleId(title.id)}
              >
                + 회차 추가
              </button>
            ))}
            </details>
          </li>
          );
        })}
        </ul>
      </div>
      {confirmState && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-sm rounded-2xl border border-border/50 bg-card p-5 shadow-lg">
            <p className="text-sm text-foreground">{confirmState.message}</p>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setConfirmState(null)} className={rerunBtnClass}>
                취소
              </button>
              <button
                onClick={confirmState.onConfirm}
                className={`${smallBtnBase} border-destructive bg-destructive text-destructive-foreground hover:bg-destructive/90`}
              >
                확인
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
