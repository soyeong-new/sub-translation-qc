// 작품을 등록하고 영상/SRT 파일을 업로드해 분석을 시작하는 화면.

import { useEffect, useRef, useState } from "react";
import {
  createTitle, createEpisode, createTargetVersion, runAnalysis, uploadVideo, uploadSrt,
  getTargetVersion,
} from "../api.js";
import FileDropzone from "../components/FileDropzone.jsx";

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

const inputClass =
  "block w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground " +
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
  const [videoFile, setVideoFile] = useState(null);
  const [srtFile, setSrtFile] = useState(null);
  const [videoProgress, setVideoProgress] = useState(null);
  const [srtProgress, setSrtProgress] = useState(null);
  const [status, setStatus] = useState(null); // { kind: "loading" | "success" | "error", message: string }
  const isSubmitting = status?.kind === "loading";
  const canSubmit = Boolean(name && videoFile && srtFile) && !isSubmitting;

  // 컴포넌트가 언마운트된 뒤에는 폴링을 멈추고 setState도 호출하지 않도록
  // 마운트 여부를 추적한다 (FileDropzone.jsx 등 다른 비동기 처리와 동일한 패턴).
  const isMountedRef = useRef(true);
  useEffect(() => {
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  function pollUntilDone(targetVersionId) {
    return new Promise((resolve, reject) => {
      const poll = async () => {
        if (!isMountedRef.current) return;
        try {
          const tv = await getTargetVersion(targetVersionId);
          if (!isMountedRef.current) return;
          if (tv.status === "review") {
            resolve();
          } else if (tv.status === "failed") {
            reject(new Error(tv.error_message ?? "분석 중 오류가 발생했습니다."));
          } else {
            setTimeout(poll, 2000);
          }
        } catch (err) {
          if (isMountedRef.current) reject(err);
        }
      };
      poll();
    });
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
      const episode = await createEpisode(title.id, null, videoUpload.path);
      const tv = await createTargetVersion(episode.id, "es", "LATAM");
      setStatus({ kind: "loading", message: "분석 중... (STT + 번역검토 진행중, 시간이 걸릴 수 있습니다)" });
      await runAnalysis(tv.id, srtUpload.path);
      await pollUntilDone(tv.id);
      setStatus({ kind: "success", message: "완료" });
      onSelect(tv.id);
    } catch (err) {
      setStatus({ kind: "error", message: err.message ?? "요청 중 오류가 발생했습니다." });
    } finally {
      setVideoProgress(null);
      setSrtProgress(null);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-md rounded-lg border border-border bg-card p-8 shadow-sm">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold text-card-foreground">작품 등록</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            한국어 원본과 스페인어 번역본을 등록하고 QC 분석을 시작합니다.
          </p>
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
            label="스페인어 SRT 자막"
            accept={SRT_EXTENSIONS.join(",")}
            file={srtFile}
            onFileSelected={handleSrtSelected}
            progress={srtProgress}
            disabled={isSubmitting}
          />

          <button
            type="submit"
            disabled={!canSubmit}
            className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-primary
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
    </div>
  );
}
