// 등록된 작품(타이틀명 아카이브) — 왼쪽 목록에서 title을 고르면 오른쪽에
// 상세(캐릭터 성별/용어집/회차별 언어)가 표시되는 마스터-디테일 레이아웃.
// "+ New Title"을 누르면 오른쪽 자리에 등록 폼이 뜬다. 회차별로 새 언어
// 버전을 추가할 수도 있다(같은 title_id 아래 묶여야 성별 재사용 힌트가 연결된다).

import { useEffect, useRef, useState } from "react";
import {
  listTitles, deleteTitle, deleteTargetVersion, rerunAnalysis, pollTargetVersionStatus, getStorageUsage,
  listLanguageProfiles, uploadSrt, uploadSrtKo, uploadVideo, createTitle, createEpisode,
  createTargetVersion, runAnalysis, updateTitleType, updateTitleName, updateCharacterGender,
  postGlossaryEntry, patchGlossaryEntry, deleteGlossaryEntry,
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

// 용어집 피벗 테이블의 언어 컬럼들 — 이 title 아래 실제로 존재하는
// (언어, variant) 조합만 보여준다(전역 언어 목록이 아니라).
function glossaryLanguageColumns(title) {
  const keys = new Set();
  title.episodes.forEach((ep) => {
    ep.target_versions.forEach((tv) => {
      keys.add(`${tv.target_language}_${tv.variant}`);
    });
  });
  return Array.from(keys);
}

// 사이드바 목록 항목의 보조 설명 줄 — "드라마 · 8화 · 2개 언어" 같은 요약.
function titleSubtitle(title) {
  const parts = [title.type === "movie" ? "영화" : "드라마"];
  if (title.type === "series") parts.push(`${title.episodes.length}화`);
  const langCount = glossaryLanguageColumns(title).length;
  if (langCount > 0) parts.push(`${langCount}개 언어`);
  return parts.join(" · ");
}

// 오른쪽 상세 패널에 지금 뭘 보여줄지 — title id(문자열), "new"(등록 폼),
// null(아무것도 선택 안 함). 새로고침해도 유지되도록 localStorage에 저장한다.
const SELECTED_STORAGE_KEY = "qc_archive_selected";

function loadSelection() {
  try {
    return localStorage.getItem(SELECTED_STORAGE_KEY) || null;
  } catch {
    return null;
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

// 사이드바 폭이 좁아 범례가 한 줄에 들어와야 해서 축약한 라벨. 전체 문구는
// title 속성으로 hover 시 보여준다.
const STATUS_SHORT_LABELS = {
  analyzing: "분석중",
  awaiting_confirmation: "확인필요",
  review: "검토가능",
  failed: "실패",
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

const STATUS_STYLES = {
  loading: "text-muted-foreground",
  success: "text-success",
  error: "text-destructive",
};

const smallBtnBase =
  "inline-flex items-center justify-center rounded-lg border px-2.5 py-1 text-xs font-medium " +
  "transition-all active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
  "disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100";
const openBtnClass = `${smallBtnBase} border-transparent bg-primary/10 text-primary hover:bg-primary/20`;
const primarySolidBtnClass = `${smallBtnBase} border-transparent bg-primary font-semibold text-primary-foreground hover:bg-primary/90`;
const rerunBtnClass = `${smallBtnBase} border-input bg-background text-foreground hover:bg-accent`;
const addLangBtnClass =
  "inline-flex items-center justify-center gap-1.5 rounded-lg border border-dashed border-input px-3 py-1.5 text-xs " +
  "font-medium text-muted-foreground transition-all hover:bg-accent hover:text-foreground active:scale-95 " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";
const deleteBtnClass =
  "inline-flex h-6 w-6 items-center justify-center text-muted-foreground leading-none " +
  "transition-all hover:text-destructive active:scale-90 focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100";
const inputClass =
  "block w-full rounded-lg border border-input bg-background px-2.5 py-1.5 text-xs text-foreground " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";
const newTitleBtnClass =
  "inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r from-[#2A5BFF] via-[#7A2DFF] to-[#FF3B30] " +
  "px-3 py-2 text-sm font-semibold text-white shadow-md shadow-primary/15 transition-transform active:scale-[0.98] " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background";

// New Title 폼 전용 스타일 — 목록의 인라인 폼들보다 더 눈에 띄어야 해서(주된
// 등록 흐름) 위 inputClass보다 크게 잡는다.
const formInputClass =
  "block w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground " +
  "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background " +
  "disabled:cursor-not-allowed disabled:opacity-50";
const formLabelClass = "mb-1.5 block text-sm font-medium text-foreground";

function Field({ id, label, children }) {
  return (
    <div>
      <label htmlFor={id} className={formLabelClass}>
        {label}
      </label>
      {children}
    </div>
  );
}

// 브라우저 기본 <details>는 open/close를 순간적으로 처리해 transition이
// 안 먹어서, open 상태를 직접 관리하고 grid-template-rows를 0fr<->1fr로
// 움직이는 방식으로 부드럽게 펼쳐지게 한다.
function Disclosure({ summary, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-1.5 border-b border-border bg-muted px-4 py-3 text-left text-xs font-semibold text-foreground"
      >
        <span>{summary}</span>
        <svg
          viewBox="0 0 20 20"
          fill="currentColor"
          className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`}
        >
          <path
            fillRule="evenodd"
            d="M6 4a1 1 0 0 1 1.7-.7l5 5a1 1 0 0 1 0 1.4l-5 5A1 1 0 0 1 6 14V4Z"
            clipRule="evenodd"
          />
        </svg>
      </button>
      <div
        className={`grid transition-[grid-template-rows] duration-200 ease-out ${
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
        }`}
      >
        <div className="overflow-hidden">{children}</div>
      </div>
    </div>
  );
}

function AddLanguageForm({ episodes, selectedEpisodeId, onSelectEpisode, availableProfiles,
                            selectedProfile, onSelectProfile,
                            srtFile, onSrtSelected, progress, status, onSubmit, onCancel }) {
  const isSubmitting = status?.kind === "loading";
  const canSubmit = Boolean(selectedProfile && srtFile) && !isSubmitting;

  return (
    <div className="mt-2 animate-fade-slide-in space-y-3 rounded-xl border border-dashed border-border bg-muted/50 p-3.5">
      <div className="flex items-end gap-2">
        {episodes.length > 1 && (
          <div className="min-w-0 shrink-0">
            <label htmlFor="add-language-episode" className="mb-1.5 block whitespace-nowrap text-xs font-medium text-foreground">
              회차
            </label>
            <select
              id="add-language-episode"
              value={selectedEpisodeId ?? ""}
              onChange={(e) => onSelectEpisode(e.target.value)}
              disabled={isSubmitting}
              className={inputClass}
            >
              {episodes.map((ep) => (
                <option key={ep.id} value={ep.id}>{ep.episode_no != null ? `${ep.episode_no}화` : "회차 미지정"}</option>
              ))}
            </select>
          </div>
        )}
        <div className="min-w-0 flex-1">
          <label htmlFor="add-language-select" className="mb-1.5 block whitespace-nowrap text-xs font-medium text-foreground">
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
      <button type="button" onClick={onSubmit} disabled={!canSubmit} className={primarySolidBtnClass}>
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
    <div className="mt-3 animate-fade-slide-in space-y-2 rounded-xl border border-dashed border-border/70 bg-background/50 p-3">
      <div className="flex items-end gap-2">
        <div className="w-20 shrink-0">
          <label htmlFor="add-episode-no" className="mb-1.5 block whitespace-nowrap text-xs font-medium text-foreground">
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
          <label htmlFor="add-episode-lang" className="mb-1.5 block whitespace-nowrap text-xs font-medium text-foreground">
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
      <button type="button" onClick={handleSubmit} disabled={!canSubmit} className={primarySolidBtnClass}>
        회차 추가 및 분석 시작
      </button>
    </div>
  );
}

// title 하나를 새로 등록하는 폼 — 오른쪽 상세 패널에서 "+ New Title" 클릭 시 표시된다.
function NewTitleForm({ languageProfiles, isMountedRef, onCreated, onCancel }) {
  const [name, setName] = useState("");
  const [type, setType] = useState("movie");
  const [episodeNo, setEpisodeNo] = useState("");
  const [videoFile, setVideoFile] = useState(null);
  const [srtFile, setSrtFile] = useState(null);
  const [koreanSrtFile, setKoreanSrtFile] = useState(null);
  const [koreanSrtProgress, setKoreanSrtProgress] = useState(null);
  const [videoProgress, setVideoProgress] = useState(null);
  const [srtProgress, setSrtProgress] = useState(null);
  const [status, setStatus] = useState(null);
  const [selectedProfile, setSelectedProfile] = useState(null);
  const isSubmitting = status?.kind === "loading";
  const canSubmit = Boolean(name && videoFile && srtFile && selectedProfile) && !isSubmitting;

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

  function handleSrtSelected(selected) {
    if (!SRT_EXTENSIONS.includes(getExtension(selected.name))) {
      setStatus({
        kind: "error",
        message: `지원하지 않는 자막 파일 형식입니다. (허용: ${SRT_EXTENSIONS.join(", ")})`,
      });
      return;
    }
    setStatus(null);
    setSrtFile(selected);
  }

  function handleKoreanSrtSelected(selected) {
    if (!SRT_EXTENSIONS.includes(getExtension(selected.name))) {
      setStatus({
        kind: "error",
        message: `지원하지 않는 자막 파일 형식입니다. (허용: ${SRT_EXTENSIONS.join(", ")})`,
      });
      return;
    }
    setStatus(null);
    setKoreanSrtFile(selected);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setVideoProgress(0);
    setSrtProgress(0);
    setStatus({ kind: "loading", message: "업로드 중..." });
    try {
      const [videoUpload, srtUpload] = await Promise.all([
        uploadVideo(videoFile, setVideoProgress),
        uploadSrt(srtFile, setSrtProgress),
      ]);
      setStatus({ kind: "loading", message: "등록 중..." });
      const title = await createTitle(name, type);
      // 한국어 SRT는 선택 입력이지만, 업로드가 실패하면 STT 자체 인식
      // 텍스트(정확도가 더 낮음)로 조용히 되돌아가 버린다 — 사용자가 SRT를
      // 올릴 때 의도한 바가 아니므로, (영어 SRT와 달리) 업로드 실패를
      // 조용히 무시하지 않고 등록 자체를 막는다.
      let koreanSrtPath = null;
      if (koreanSrtFile) {
        setStatus({ kind: "loading", message: "한국어 SRT 업로드 중..." });
        const koreanSrtUpload = await uploadSrtKo(koreanSrtFile, setKoreanSrtProgress);
        koreanSrtPath = koreanSrtUpload.path;
      }
      const episodeNoValue = type === "series" && episodeNo.trim() ? Number(episodeNo) : null;
      const episode = await createEpisode(
        title.id, episodeNoValue, videoUpload.path, koreanSrtPath,
      );
      const tv = await createTargetVersion(episode.id, selectedProfile.language, selectedProfile.variant);
      setStatus({ kind: "loading", message: "분석 중..." });
      await runAnalysis(tv.id, srtUpload.path);
      const doneStatus = await pollTargetVersionStatus(tv.id, { isMounted: () => isMountedRef.current });
      if (!isMountedRef.current) return;
      setStatus({ kind: "success", message: "완료" });
      onCreated(tv.id, doneStatus, title.id);
    } catch (err) {
      if (isMountedRef.current) {
        setStatus({ kind: "error", message: err.message ?? "요청 중 오류가 발생했습니다." });
      }
    } finally {
      if (isMountedRef.current) {
        setVideoProgress(null);
        setSrtProgress(null);
        setKoreanSrtProgress(null);
      }
    }
  }

  return (
    <div className="mx-auto max-w-xl">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-card-foreground">새 작품 등록</h1>
        <button type="button" onClick={onCancel} disabled={isSubmitting} className={rerunBtnClass}>
          취소
        </button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-5">
        <Field id="title-name" label="작품명">
          <input
            id="title-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="예: 오징어 게임"
            required
            disabled={isSubmitting}
            className={formInputClass}
          />
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field id="title-type" label="유형">
            <select
              id="title-type"
              value={type}
              onChange={(e) => setType(e.target.value)}
              disabled={isSubmitting}
              className={formInputClass}
            >
              <option value="movie">영화</option>
              <option value="series">드라마</option>
            </select>
          </Field>

          <Field id="target-language" label="대상언어">
            <select
              id="target-language"
              value={selectedProfile ? profileKey(selectedProfile) : ""}
              onChange={(e) => {
                const match = languageProfiles.find((p) => profileKey(p) === e.target.value);
                setSelectedProfile(match ?? null);
              }}
              disabled={isSubmitting || languageProfiles.length === 0}
              className={formInputClass}
            >
              {languageProfiles.length === 0 && <option value="">불러오는 중...</option>}
              {languageProfiles.length > 0 && <option value="">언어 선택...</option>}
              {languageProfiles.map((p) => (
                <option key={profileKey(p)} value={profileKey(p)}>
                  {p.display_name}
                </option>
              ))}
            </select>
          </Field>
        </div>

        {type === "series" && (
          <Field id="episode-no" label="회차">
            <input
              id="episode-no"
              type="number"
              value={episodeNo}
              onChange={(e) => setEpisodeNo(e.target.value)}
              placeholder="예: 1"
              disabled={isSubmitting}
              className={formInputClass}
            />
          </Field>
        )}

        <FileDropzone
          id="video-file"
          label="한국어 원본 영상"
          accept={VIDEO_EXTENSIONS.join(",")}
          file={videoFile}
          onFileSelected={handleVideoSelected}
          progress={videoProgress}
          disabled={isSubmitting}
        />

        <FileDropzone
          id="srt-file"
          label="대상언어 SRT 자막"
          accept={SRT_EXTENSIONS.join(",")}
          file={srtFile}
          onFileSelected={handleSrtSelected}
          progress={srtProgress}
          disabled={isSubmitting}
        />

        <FileDropzone
          id="korean-srt-file"
          label="한국어 SRT 자막 (선택)"
          accept={SRT_EXTENSIONS.join(",")}
          file={koreanSrtFile}
          onFileSelected={handleKoreanSrtSelected}
          progress={koreanSrtProgress}
          disabled={isSubmitting}
        />

        <button
          type="submit"
          disabled={!canSubmit}
          className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary
            px-4 py-2 text-sm font-medium text-primary-foreground transition-colors
            hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2
            focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background
            disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isSubmitting && (
            <span
              aria-hidden="true"
              className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground/40 border-t-primary-foreground"
            />
          )}
          분석 시작
        </button>

        {status && (
          <p
            role="status"
            aria-live="polite"
            className={`text-sm ${STATUS_STYLES[status.kind]}`}
          >
            {status.message}
          </p>
        )}
      </form>
    </div>
  );
}

// 용어집 표기 셀 — 클릭하면 입력창으로 바뀌고, blur 시 값이 바뀌었을 때만
// PATCH를 보낸다(불필요한 요청 방지).
function GlossarySpellingCell({ entry, columnKey, onSaved, onError }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(entry.spellings[columnKey] || "");

  if (!editing) {
    return (
      <span className="block cursor-pointer text-foreground" onClick={() => setEditing(true)}>
        {entry.spellings[columnKey] || <span className="text-muted-foreground">—</span>}
      </span>
    );
  }
  return (
    <input
      className="w-full rounded border border-input bg-background px-1 py-0.5 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      autoFocus
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={async () => {
        setEditing(false);
        if (value !== (entry.spellings[columnKey] || "")) {
          try {
            await patchGlossaryEntry(entry.id, { spellings: { [columnKey]: value } });
            onSaved();
          } catch (err) {
            onError(err.message ?? "표기 저장 중 오류가 발생했습니다.");
          }
        }
      }}
    />
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

  // "언어 추가" 폼은 title당 하나만 열리고, 안에서 회차를 골라 대상 episode를 정한다.
  const [addLanguageEpisodeId, setAddLanguageEpisodeId] = useState(null);
  const [addLanguageProfile, setAddLanguageProfile] = useState(null);
  const [addLanguageSrtFile, setAddLanguageSrtFile] = useState(null);
  const [addLanguageProgress, setAddLanguageProgress] = useState(null);
  const [addLanguageStatus, setAddLanguageStatus] = useState(null);

  // 오른쪽 상세 패널에 표시할 대상 — title id | "new" | null.
  const [selection, setSelectionState] = useState(loadSelection);
  const [confirmState, setConfirmState] = useState(null); // { message, onConfirm } | null
  const [editingName, setEditingName] = useState(false);
  const [newGlossaryTerm, setNewGlossaryTerm] = useState("");
  const [newGlossaryError, setNewGlossaryError] = useState(null);

  function setSelection(value) {
    setSelectionState(value);
    setEditingName(false);
    setNewGlossaryTerm("");
    setNewGlossaryError(null);
    if (value) localStorage.setItem(SELECTED_STORAGE_KEY, value);
    else localStorage.removeItem(SELECTED_STORAGE_KEY);
  }

  const skipNameSaveRef = useRef(false);
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
      .catch(() => {}); // 실패해도 "언어 추가"/등록 폼만 못 쓰게 될 뿐이라 조용히 무시
  }, []);

  // 등록된 title이 하나도 없으면(첫 사용) 안내 없이 바로 등록 폼을 보여준다 —
  // 예전엔 목록이 비어도 등록 폼이 항상 화면에 떠 있었던 것과 동일한 흐름.
  useEffect(() => {
    if (titles && titles.length === 0 && selection === null) {
      setSelectionState("new");
    }
  }, [titles]);

  async function waitThenOpen(targetVersionId, titleId) {
    setBusyId(targetVersionId);
    setError(null);
    try {
      const status = await pollTargetVersionStatus(targetVersionId, {
        isMounted: () => isMountedRef.current,
      });
      onOpen(targetVersionId, status, titleId);
    } catch (err) {
      setError(err.message ?? "요청 중 오류가 발생했습니다.");
    } finally {
      if (isMountedRef.current) setBusyId(null);
    }
  }

  function handleOpen(tv, titleId) {
    if (tv.status === "review" || tv.status === "awaiting_confirmation") {
      onOpen(tv.id, tv.status, titleId);
      return;
    }
    waitThenOpen(tv.id, titleId);
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

  async function handleChangeName(title, newName) {
    const trimmed = newName.trim();
    setEditingName(false);
    if (!trimmed || trimmed === title.name) return;
    setBusyId(title.id);
    setError(null);
    try {
      await updateTitleName(title.id, trimmed);
      refresh();
    } catch (err) {
      setError(err.message ?? "제목 변경 중 오류가 발생했습니다.");
    } finally {
      if (isMountedRef.current) setBusyId(null);
    }
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

  async function submitNewGlossaryEntry(titleId) {
    const koreanTerm = newGlossaryTerm.trim();
    if (!koreanTerm) return;
    setNewGlossaryError(null);
    try {
      await postGlossaryEntry(titleId, { korean_term: koreanTerm, category: "person", aliases: [] });
      setNewGlossaryTerm("");
      refresh();
    } catch (err) {
      setNewGlossaryError(err.message ?? "용어 추가 중 오류가 발생했습니다.");
    }
  }

  function onGlossaryChanged() {
    refresh();
  }

  async function onDeleteGlossaryEntry(entryId) {
    if (!window.confirm("이 용어를 삭제할까요?")) return;
    try {
      await deleteGlossaryEntry(entryId);
      refresh();
    } catch (err) {
      setError(err.message ?? "용어 삭제 중 오류가 발생했습니다.");
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
      if (selection === title.id) setSelection(null);
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

  async function handleAddLanguageSubmit(episodeId, titleId) {
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
      onOpen(tv.id, doneStatus, titleId);
    } catch (err) {
      if (isMountedRef.current) {
        setAddLanguageStatus({ kind: "error", message: err.message ?? "언어 추가 중 오류가 발생했습니다." });
      }
    } finally {
      if (isMountedRef.current) setAddLanguageProgress(null);
    }
  }

  if (titles === null) return null; // 로딩 중

  const filteredTitles = filterType === "all" ? titles : titles.filter((t) => t.type === filterType);
  const selectedTitle =
    selection && selection !== "new" ? titles.find((t) => t.id === selection) ?? null : null;

  return (
    <div className="flex min-h-0 w-full flex-1 gap-4">
      {/* 왼쪽: title 목록 */}
      <div className="flex w-72 shrink-0 flex-col gap-3 rounded-xl border border-border bg-card p-3">
        <button type="button" onClick={() => setSelection("new")} className={newTitleBtnClass}>
          <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4">
            <path d="M10 4a.75.75 0 0 1 .75.75v4.5h4.5a.75.75 0 0 1 0 1.5h-4.5v4.5a.75.75 0 0 1-1.5 0v-4.5h-4.5a.75.75 0 0 1 0-1.5h4.5v-4.5A.75.75 0 0 1 10 4Z" />
          </svg>
          새 작품 등록
        </button>

        <div className="flex gap-1 rounded-lg bg-muted p-1 text-xs">
          {TYPE_TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setFilterType(tab.key)}
              className={`flex-1 rounded-md px-2 py-1.5 text-center transition-colors ${
                filterType === tab.key
                  ? "bg-card font-medium text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {error && (
          <p role="status" aria-live="polite" className="text-sm text-destructive">{error}</p>
        )}

        <ul className="flex-1 space-y-1 overflow-y-auto">
          {filteredTitles.map((title) => {
            const isSelected = selection === title.id;
            return (
              <li key={title.id}>
                <div
                  className={`group flex items-center gap-2 rounded-lg py-2 pl-3 pr-2 transition-colors ${
                    isSelected ? "bg-primary/10" : "hover:bg-muted"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => setSelection(title.id)}
                    className="min-w-0 flex-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <p className="truncate text-sm font-medium text-foreground">{title.name}</p>
                    <p className="truncate text-[11px] text-muted-foreground">{titleSubtitle(title)}</p>
                  </button>
                  <button
                    disabled={busyId === title.id}
                    onClick={() => handleDelete(title)}
                    aria-label="삭제"
                    className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100 disabled:opacity-50"
                  >
                    ×
                  </button>
                </div>
              </li>
            );
          })}
        </ul>

        {storage && (
          <div className="border-t border-border pt-2.5">
            <div className="mb-1.5 flex items-center justify-between text-[11px] text-muted-foreground">
              <span>저장공간</span>
              <span>
                고정 {formatGB(storage.used - storage.media_used)}GB + 영상 {formatGB(storage.media_used)}GB
                {" "}/ 잔여 {formatGB(storage.total - storage.used)}GB
              </span>
            </div>
            <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-muted">
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
      </div>

      {/* 오른쪽: 선택된 title 상세 / 등록 폼 */}
      <div className="min-w-0 flex-1 overflow-y-auto rounded-xl border border-border bg-card p-7">
        {selection === "new" && (
          <NewTitleForm
            languageProfiles={languageProfiles}
            isMountedRef={isMountedRef}
            onCreated={(tvId, status, titleId) => onOpen(tvId, status, titleId)}
            onCancel={() => setSelection(null)}
          />
        )}

        {selection !== "new" && !selectedTitle && (
          <p className="text-sm text-muted-foreground">왼쪽에서 작품을 선택하세요.</p>
        )}

        {selectedTitle && (() => {
          const title = selectedTitle;
          return (
            <div>
              <div className="mb-5 flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2.5">
                  {editingName ? (
                    <input
                      type="text"
                      autoFocus
                      defaultValue={title.name}
                      disabled={busyId === title.id}
                      onFocus={(e) => e.target.select()}
                      onBlur={(e) => {
                        if (skipNameSaveRef.current) {
                          skipNameSaveRef.current = false;
                          return;
                        }
                        handleChangeName(title, e.target.value);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") e.target.blur();
                        if (e.key === "Escape") {
                          skipNameSaveRef.current = true;
                          setEditingName(false);
                        }
                      }}
                      className="min-w-0 flex-1 rounded-lg border border-input bg-background px-2 py-0.5 text-[26px] font-semibold tracking-tight text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                    />
                  ) : (
                    <h2
                      onClick={() => setEditingName(true)}
                      title="클릭해서 제목 수정"
                      className="cursor-pointer truncate text-[26px] font-semibold tracking-tight text-foreground hover:underline"
                    >
                      {title.name}
                    </h2>
                  )}
                  <select
                    aria-label="유형"
                    value={title.type}
                    disabled={busyId === title.id}
                    onChange={(e) => handleChangeType(title, e.target.value)}
                    className="shrink-0 rounded-md border-none bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <option value="movie">영화</option>
                    <option value="series">드라마</option>
                  </select>
                </div>
                <div className="flex shrink-0 items-center gap-3 whitespace-nowrap rounded-full bg-muted px-3 py-1.5 text-[11px] leading-none text-muted-foreground">
                  {STATUS_LEGEND.map(({ key, dot }) => (
                    <span key={key} title={STATUS_LABELS[key]} className="inline-flex items-center gap-1.5">
                      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
                      {STATUS_SHORT_LABELS[key]}
                    </span>
                  ))}
                </div>
              </div>

              {title.character_genders?.length > 0 && (
                <div className="mb-3 overflow-hidden rounded-xl border border-border">
                  <Disclosure
                    summary={
                      <>
                        캐릭터 성별 <span className="font-normal text-muted-foreground">· {title.character_genders.length}명</span>
                      </>
                    }
                  >
                    <div className="flex flex-wrap gap-1.5 px-4 py-3.5">
                      {title.character_genders.map((fact) => (
                        <span
                          key={fact.id}
                          className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs text-foreground"
                        >
                          {fact.character_name}
                          <select
                            aria-label={`${fact.character_name} 성별`}
                            value={fact.gender}
                            disabled={busyId === fact.id}
                            onChange={(e) => handleChangeCharacterGender(fact, e.target.value)}
                            className={`rounded-full border-none px-1.5 py-0.5 text-[10px] font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 ${
                              fact.gender === "female" ? "bg-violet-500/10 text-violet-600" : "bg-primary/10 text-primary"
                            }`}
                          >
                            <option value="male">M</option>
                            <option value="female">F</option>
                          </select>
                        </span>
                      ))}
                    </div>
                  </Disclosure>
                </div>
              )}

              <div className="mb-6 overflow-hidden rounded-xl border border-border">
                <Disclosure
                  summary={
                    <>
                      용어집 <span className="font-normal text-muted-foreground">· {title.glossary.length}개</span>
                    </>
                  }
                >
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse text-xs">
                      <thead>
                        <tr className="border-b border-border bg-card text-foreground">
                          <th className="px-4 py-2 text-left font-bold">한국어 용어</th>
                          {glossaryLanguageColumns(title).map((col) => (
                            <th key={col} className="px-4 py-2 text-left font-bold">
                              {col}
                            </th>
                          ))}
                          <th className="w-8"></th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border/40">
                        {[...title.glossary].sort((a, b) => a.korean_term.localeCompare(b.korean_term, "ko")).map((entry) => (
                          <tr key={entry.id} className="hover:bg-accent/40">
                            <td className="px-4 py-2 text-foreground">{entry.korean_term}</td>
                            {glossaryLanguageColumns(title).map((col) => (
                              <td key={col} className="px-4 py-2">
                                <GlossarySpellingCell entry={entry} columnKey={col} onSaved={onGlossaryChanged} onError={setError} />
                              </td>
                            ))}
                            <td className="px-4 py-2">
                              <button
                                type="button"
                                aria-label={`${entry.korean_term} 삭제`}
                                onClick={() => onDeleteGlossaryEntry(entry.id)}
                                className={deleteBtnClass}
                              >
                                ×
                              </button>
                            </td>
                          </tr>
                        ))}
                        <tr>
                          <td className="px-4 py-2" colSpan={glossaryLanguageColumns(title).length + 2}>
                            <input
                              type="text"
                              value={newGlossaryTerm}
                              onChange={(e) => {
                                setNewGlossaryTerm(e.target.value);
                                setNewGlossaryError(null);
                              }}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") submitNewGlossaryEntry(title.id);
                              }}
                              placeholder="+ 새 용어 입력 후 Enter"
                              className="w-full bg-transparent text-foreground placeholder:text-muted-foreground focus-visible:outline-none"
                            />
                            {newGlossaryError && (
                              <p role="status" aria-live="polite" className="mt-1 text-xs text-destructive">
                                {newGlossaryError}
                              </p>
                            )}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </Disclosure>
              </div>

              <div className="mb-5">
                <div className="flex flex-wrap gap-2">
                  {languageProfiles.length > 0 && addLanguageEpisodeId === null && (
                    <button
                      className={addLangBtnClass}
                      onClick={() => openAddLanguage(title.episodes[0]?.id ?? null)}
                    >
                      + 언어 추가
                    </button>
                  )}
                  {title.type === "series" && addEpisodeTitleId === null && (
                    <button className={addLangBtnClass} onClick={() => setAddEpisodeTitleId(title.id)}>
                      + 회차 추가
                    </button>
                  )}
                </div>
                {addLanguageEpisodeId !== null && (() => {
                  const targetEpisode = title.episodes.find((ep) => ep.id === addLanguageEpisodeId);
                  const usedKeys = new Set(
                    (targetEpisode?.target_versions ?? []).map((tv) => `${tv.target_language}_${tv.variant}`));
                  const availableProfiles = languageProfiles.filter((p) => !usedKeys.has(profileKey(p)));
                  return (
                    <AddLanguageForm
                      episodes={title.episodes}
                      selectedEpisodeId={addLanguageEpisodeId}
                      onSelectEpisode={(episodeId) => {
                        setAddLanguageEpisodeId(episodeId);
                        setAddLanguageProfile(null);
                      }}
                      availableProfiles={availableProfiles}
                      selectedProfile={addLanguageProfile}
                      onSelectProfile={setAddLanguageProfile}
                      srtFile={addLanguageSrtFile}
                      onSrtSelected={handleAddLanguageSrtSelected}
                      progress={addLanguageProgress}
                      status={addLanguageStatus}
                      onSubmit={() => handleAddLanguageSubmit(addLanguageEpisodeId, title.id)}
                      onCancel={closeAddLanguage}
                    />
                  );
                })()}
                {addEpisodeTitleId === title.id && (
                  <AddEpisodeForm
                    titleId={title.id}
                    languageProfiles={languageProfiles}
                    isMountedRef={isMountedRef}
                    onDone={(tvId, status) => {
                      setAddEpisodeTitleId(null);
                      refresh();
                      onOpen(tvId, status, title.id);
                    }}
                    onCancel={() => setAddEpisodeTitleId(null)}
                  />
                )}
              </div>

              <div className="space-y-2">
                {title.episodes.map((ep) => {
                  return (
                    <div key={ep.id} className="rounded-xl bg-muted/70 p-3.5">
                      <div className="mb-2 flex items-center justify-between gap-2 text-xs font-semibold text-muted-foreground">
                        <span>{ep.episode_no != null ? `${ep.episode_no}화` : ""}</span>
                        <div className="flex items-center gap-2">
                          {ep.target_versions.map((tv) => (
                            <button
                              key={tv.id}
                              disabled={busyId === tv.id}
                              onClick={() => handleDeleteVersion(tv)}
                              aria-label={`${tv.display_name} 삭제`}
                              title={`${tv.display_name} 삭제`}
                              className="font-normal text-muted-foreground transition-colors hover:text-destructive disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              ×
                            </button>
                          ))}
                        </div>
                      </div>
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
                              className={`h-2 w-2 rounded-full ${
                                STATUS_DOT_CLASS[tv.status] || "bg-muted-foreground/50"
                              }`}
                            />
                            <span className="whitespace-nowrap rounded-md bg-card px-2.5 py-1 text-muted-foreground shadow-sm">
                              {tv.display_name}
                            </span>
                            {tv.reviewers.length > 0 && (
                              <span className="rounded-md bg-card px-2.5 py-1 text-muted-foreground shadow-sm">
                                {tv.reviewers.join(", ")}
                              </span>
                            )}
                            <button disabled={busyId === tv.id} onClick={() => handleOpen(tv, title.id)} className={`ml-auto ${openBtnClass}`}>
                              열기
                            </button>
                            <button disabled={busyId === tv.id} onClick={() => handleRerun(tv)} className={rerunBtnClass}>
                              재분석
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })()}
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
