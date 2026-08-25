// 작품을 등록하고 영상/SRT 파일을 업로드해 분석을 시작하는 화면.

import { useEffect, useRef, useState } from "react";
import {
  createTitle, createEpisode, createTargetVersion, runAnalysis, uploadVideo, uploadSrt,
  listLanguageProfiles, uploadSrtKo, pollTargetVersionStatus,
} from "../api.js";
import FileDropzone from "../components/FileDropzone.jsx";
import TitleArchiveList from "./TitleArchiveList.jsx";

const STATUS_STYLES = {
  loading: "text-muted-foreground",
  success: "text-success",
  error: "text-destructive",
};

// 백엔드 허용 확장자(backend/app/core/uploads.py)와 동기화되어야 함.
const VIDEO_EXTENSIONS = [".mp4", ".mov", ".mkv", ".avi"];
const SRT_EXTENSIONS = [".srt"];

function getExtension(filename) {
  const idx = filename.lastIndexOf(".");
  return idx === -1 ? "" : filename.slice(idx).toLowerCase();
}

// <select>의 value/React key로만 쓰이는 안정적인 문자열. 실제 language/variant는
// 이 문자열을 파싱하지 않고 languageProfiles 배열에서 매칭해 얻는다 — variant에
// "_"가 포함될 수 있어(예: "BR_formal") 문자열을 되돌려 split하면 값이 깨진다.
function profileKey(p) {
  return `${p.language}_${p.variant}`;
}

const inputClass =
  "block w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground " +
  "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background " +
  "disabled:cursor-not-allowed disabled:opacity-50";

const labelClass = "mb-1.5 block text-sm font-medium text-foreground";

function Field({ id, label, children }) {
  return (
    <div>
      <label htmlFor={id} className={labelClass}>
        {label}
      </label>
      {children}
    </div>
  );
}

export default function TitleListView({ onSelect }) {
  const [name, setName] = useState("");
  const [type, setType] = useState("movie");
  const [episodeNo, setEpisodeNo] = useState("");
  const [videoFile, setVideoFile] = useState(null);
  const [srtFile, setSrtFile] = useState(null);
  const [koreanSrtFile, setKoreanSrtFile] = useState(null);
  const [koreanSrtProgress, setKoreanSrtProgress] = useState(null);
  const [videoProgress, setVideoProgress] = useState(null);
  const [srtProgress, setSrtProgress] = useState(null);
  const [status, setStatus] = useState(null); // { kind: "loading" | "success" | "error", message: string }
  const [languageProfiles, setLanguageProfiles] = useState([]);
  const [selectedProfile, setSelectedProfile] = useState(null); // { language, variant }
  const isSubmitting = status?.kind === "loading";
  const canSubmit =
    Boolean(name && videoFile && srtFile && selectedProfile) && !isSubmitting;

  // 컴포넌트가 언마운트된 뒤에는 폴링을 멈추고 setState도 호출하지 않도록
  // 마운트 여부를 추적한다 (FileDropzone.jsx 등 다른 비동기 처리와 동일한 패턴).
  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    listLanguageProfiles()
      .then((profiles) => {
        if (!isMountedRef.current) return;
        setLanguageProfiles(profiles);
        if (profiles.length > 0) {
          setSelectedProfile(profiles[0]);
        }
      })
      .catch((err) => {
        if (!isMountedRef.current) return;
        setStatus({ kind: "error", message: err.message ?? "언어 목록을 불러오지 못했습니다." });
      });
  }, []);

  function pollUntilDone(targetVersionId) {
    return pollTargetVersionStatus(targetVersionId, { isMounted: () => isMountedRef.current });
  }

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
      setStatus({ kind: "loading", message: "분석 중... (STT + 번역검토 진행중, 시간이 걸릴 수 있습니다)" });
      await runAnalysis(tv.id, srtUpload.path);
      const doneStatus = await pollUntilDone(tv.id);
      setStatus({ kind: "success", message: "완료" });
      onSelect(tv.id, doneStatus);
    } catch (err) {
      setStatus({ kind: "error", message: err.message ?? "요청 중 오류가 발생했습니다." });
    } finally {
      setVideoProgress(null);
      setSrtProgress(null);
      setKoreanSrtProgress(null);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-6xl items-stretch gap-8 bg-background px-4 py-12">
      <div className="min-w-0 max-w-2xl flex-1 rounded-2xl border border-border/50 bg-card/80 p-8 shadow-sm backdrop-blur-md">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold text-card-foreground">New Title</h1>
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
              className={inputClass}
            />
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field id="title-type" label="유형">
              <select
                id="title-type"
                value={type}
                onChange={(e) => setType(e.target.value)}
                disabled={isSubmitting}
                className={inputClass}
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
                className={inputClass}
              >
                {languageProfiles.length === 0 && <option value="">불러오는 중...</option>}
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
                className={inputClass}
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

      <TitleArchiveList onOpen={onSelect} />
    </div>
  );
}
