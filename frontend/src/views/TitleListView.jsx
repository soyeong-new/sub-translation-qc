import { useState } from "react";
import { createTitle, createEpisode, createTargetVersion, runAnalysis } from "../api.js";

// 진행 상태 표시: shadcn 스타일 뱃지 톤을 재사용해 idle/loading/success/error 4단계를 표현.
// (ui-ux-pro-max 가이드: "Submit Feedback" — loading -> success/error 상태를 명시적으로 보여줄 것)
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
  const [videoPath, setVideoPath] = useState("");
  const [srtPath, setSrtPath] = useState("");
  const [status, setStatus] = useState(null); // { kind: "loading" | "success" | "error", message: string }
  const isSubmitting = status?.kind === "loading";

  async function handleSubmit(e) {
    e.preventDefault();
    setStatus({ kind: "loading", message: "등록 중..." });
    try {
      const title = await createTitle(name, type);
      const episode = await createEpisode(title.id, null, videoPath);
      const tv = await createTargetVersion(episode.id, "es", "LATAM");
      setStatus({ kind: "loading", message: "분석 중..." });
      await runAnalysis(tv.id, srtPath);
      setStatus({ kind: "success", message: "완료" });
      onSelect(tv.id);
    } catch (err) {
      setStatus({ kind: "error", message: err.message ?? "요청 중 오류가 발생했습니다." });
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

          <Field id="video-path" label="한국어 원본 영상 경로">
            <input
              id="video-path"
              value={videoPath}
              onChange={(e) => setVideoPath(e.target.value)}
              placeholder="/media/source/episode01.ko.mp4"
              required
              disabled={isSubmitting}
              className={`${inputClass} font-mono`}
            />
          </Field>

          <Field id="srt-path" label="스페인어 SRT 경로">
            <input
              id="srt-path"
              value={srtPath}
              onChange={(e) => setSrtPath(e.target.value)}
              placeholder="/media/subs/episode01.es-419.srt"
              required
              disabled={isSubmitting}
              className={`${inputClass} font-mono`}
            />
          </Field>

          <button
            type="submit"
            disabled={isSubmitting}
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
