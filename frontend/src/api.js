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

export const createEpisode = (titleId, episodeNo, videoPath) =>
  request(`/titles/${titleId}/episodes`, {
    method: "POST",
    body: JSON.stringify({ episode_no: episodeNo, video_path: videoPath }),
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

export const listSegments = (targetVersionId) =>
  request(`/target-versions/${targetVersionId}/segments`);

export const listCharacters = (targetVersionId) =>
  request(`/target-versions/${targetVersionId}/characters`);

export const listRelationships = (targetVersionId) =>
  request(`/target-versions/${targetVersionId}/relationships`);

export const confirmGender = (characterId, gender) =>
  request(`/characters/${characterId}/confirm-gender`, {
    method: "POST", body: JSON.stringify({ gender }),
  });

export const confirmFormality = (relationshipId, formalityLevel) =>
  request(`/relationships/${relationshipId}/confirm-formality`, {
    method: "POST", body: JSON.stringify({ formality_level: formalityLevel }),
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
