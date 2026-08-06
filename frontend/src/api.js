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

export const listSegments = (targetVersionId) =>
  request(`/target-versions/${targetVersionId}/segments`);

export const getFlaggedSegments = (targetVersionId) =>
  request(`/target-versions/${targetVersionId}/flagged-segments`);

export const resolveGender = (segmentId, { characterId = null, gender = null } = {}) =>
  request(`/segments/${segmentId}/resolve-gender`, {
    method: "POST",
    body: JSON.stringify({ character_id: characterId, gender }),
  });

export const resolveFormality = (segmentId, { relationshipId = null, formalityLevel = null } = {}) =>
  request(`/segments/${segmentId}/resolve-formality`, {
    method: "POST",
    body: JSON.stringify({ relationship_id: relationshipId, formality_level: formalityLevel }),
  });

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

export const uploadChartImage = (file, onProgress) =>
  uploadWithProgress("/uploads/chart-image", file, onProgress);

export const uploadSrtEn = (file, onProgress) =>
  uploadWithProgress("/uploads/srt-en", file, onProgress);

export const listTitles = () => request("/titles");

export const getTitle = (titleId) => request(`/titles/${titleId}`);

export const attachChartImage = (titleId, imagePath) =>
  request(`/titles/${titleId}/chart-image`, {
    method: "POST", body: JSON.stringify({ image_path: imagePath }),
  });

export const listTitleCharacters = (titleId) => request(`/titles/${titleId}/characters`);

export const listTitleRelationships = (titleId) => request(`/titles/${titleId}/relationships`);

export const createTitleCharacter = (titleId, label) =>
  request(`/titles/${titleId}/characters`, {
    method: "POST", body: JSON.stringify({ label }),
  });

export const updateCharacter = (characterId, fields) =>
  request(`/characters/${characterId}`, { method: "PATCH", body: JSON.stringify(fields) });

export const deleteCharacter = (characterId) =>
  request(`/characters/${characterId}`, { method: "DELETE" });

export const createTitleRelationship = (titleId, speakerLabel, addresseeLabel, relationshipType) =>
  request(`/titles/${titleId}/relationships`, {
    method: "POST",
    body: JSON.stringify({
      speaker_label: speakerLabel, addressee_label: addresseeLabel,
      relationship_type: relationshipType,
    }),
  });

export const updateRelationship = (relationshipId, relationshipType) =>
  request(`/relationships/${relationshipId}`, {
    method: "PATCH", body: JSON.stringify({ relationship_type: relationshipType }),
  });

export const deleteRelationship = (relationshipId) =>
  request(`/relationships/${relationshipId}`, { method: "DELETE" });

export const confirmChart = (titleId) =>
  request(`/titles/${titleId}/chart/confirm`, { method: "POST" });
