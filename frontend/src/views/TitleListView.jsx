import { useState } from "react";
import {
  createTitle, createEpisode, createTargetVersion, runAnalysis, uploadVideo, uploadSrt,
} from "../api.js";
import FileDropzone from "../components/FileDropzone.jsx";

const STATUS_STYLES = {
  loading: "text-muted-foreground",
  success: "text-success",
  error: "text-destructive",
};

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
      setStatus({ kind: "loading", message: "분석 중..." });
      await runAnalysis(tv.id, srtUpload.path);
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
            accept="video/*"
            file={videoFile}
            onFileSelected={setVideoFile}
            progress={videoProgress}
            disabled={isSubmitting}
          />

          <FileDropzone
            id="srt-file"
            label="스페인어 SRT 자막"
            accept=".srt"
            file={srtFile}
            onFileSelected={setSrtFile}
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
