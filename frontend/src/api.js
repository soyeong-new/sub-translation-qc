// 백엔드 REST API를 감싸는 클라이언트 함수 모음.

const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`API 오류 ${res.status}: ${await res.text()}`);
  return res.json();
}

export const listLanguageProfiles = () => request("/language-profiles");

export const createTitle = (name, type) =>
  request("/titles", { method: "POST", body: JSON.stringify({ name, type }) });

export const createEpisode = (titleId, episodeNo, videoPath, englishSrtPath = null) =>
  request(`/titles/${titleId}/episodes`, {
    method: "POST",
    body: JSON.stringify({
      episode_no: episodeNo, video_path: videoPath, english_srt_path: englishSrtPath,
    }),
  });

export const createTargetVersion = (episodeId, targetLanguage, variant) =>
  request(`/episodes/${episodeId}/target-versions`, {
    method: "POST",
    body: JSON.stringify({ target_language: targetLanguage, variant }),
  });

export const runAnalysis = (targetVersionId, targetSrtPath) =>
  request(`/target-versions/${targetVersionId}/run-analysis`, {
    method: "POST", body: JSON.stringify({ target_srt_path: targetSrtPath }),
  });

export const getFindings = (targetVersionId) =>
  request(`/target-versions/${targetVersionId}/findings`);

export const submitReviewAction = (findingId, action, reviewerName, finalText = "") =>
  request(`/findings/${findingId}/review-action`, {
    method: "POST",
    body: JSON.stringify({ action, reviewer_name: reviewerName, final_text: finalText }),
  });

export const requeryFinding = (findingId, instruction, reviewerName) =>
  request(`/findings/${findingId}/requery`, {
    method: "POST",
    body: JSON.stringify({ instruction, reviewer_name: reviewerName }),
  });

export const listSegments = (targetVersionId) =>
  request(`/target-versions/${targetVersionId}/segments`);

export const getFlaggedSegments = (targetVersionId) =>
  request(`/target-versions/${targetVersionId}/flagged-segments`);

export const resolveGender = (segmentId, gender) =>
  request(`/segments/${segmentId}/resolve-gender`, {
    method: "POST",
    body: JSON.stringify({ gender }),
  });

export const resolveFormality = (segmentId, formalityLevel) =>
  request(`/segments/${segmentId}/resolve-formality`, {
    method: "POST",
    body: JSON.stringify({ formality_level: formalityLevel }),
  });

export const correctStt = (segmentId, correctedText, reviewerName) =>
  request(`/segments/${segmentId}/correct-stt`, {
    method: "POST",
    body: JSON.stringify({ corrected_text: correctedText, reviewer_name: reviewerName }),
  });

export const exportTargetVersion = (targetVersionId) =>
  request(`/target-versions/${targetVersionId}/export`);

export const getTargetVersion = (targetVersionId) =>
  request(`/target-versions/${targetVersionId}`);

function uploadWithProgress(path, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}${path}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        let message = `업로드 오류 ${xhr.status}: ${xhr.responseText}`;
        try {
          const parsed = JSON.parse(xhr.responseText);
          if (parsed?.detail) message = parsed.detail;
        } catch {
          // response body wasn't JSON; keep the raw-text fallback above
        }
        reject(new Error(message));
      }
    };
    xhr.onerror = () => reject(new Error("업로드 중 네트워크 오류가 발생했습니다."));
    const formData = new FormData();
    formData.append("file", file);
    xhr.send(formData);
  });
}

export const uploadVideo = (file, onProgress) =>
  uploadWithProgress("/uploads/video", file, onProgress);

export const uploadSrt = (file, onProgress) =>
  uploadWithProgress("/uploads/srt", file, onProgress);

export const uploadSrtEn = (file, onProgress) =>
  uploadWithProgress("/uploads/srt-en", file, onProgress);

export const listTitles = () => request("/titles");

export const getTitle = (titleId) => request(`/titles/${titleId}`);
