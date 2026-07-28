const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`API 오류 ${res.status}: ${await res.text()}`);
  return res.json();
}

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
