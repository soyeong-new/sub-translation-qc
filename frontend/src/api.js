// 백엔드 REST API를 감싸는 클라이언트 함수 모음.

const BASE = "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const raw = await res.text();
    let message = `API 오류 ${res.status}: ${raw}`;
    try {
      const parsed = JSON.parse(raw);
      if (parsed?.detail) message = parsed.detail;
    } catch {
      // response body wasn't JSON; keep the raw-text fallback above
    }
    throw new Error(message);
  }
  return res.json();
}

export const listLanguageProfiles = () => request("/language-profiles");

export const createTitle = (name, type) =>
  request("/titles", { method: "POST", body: JSON.stringify({ name, type }) });

export const updateTitleType = (titleId, type) =>
  request(`/titles/${titleId}`, { method: "PATCH", body: JSON.stringify({ type }) });

export const updateCharacterGender = (factId, gender) =>
  request(`/character-genders/${factId}`, { method: "PATCH", body: JSON.stringify({ gender }) });

export const createEpisode = (titleId, episodeNo, videoPath, koreanSrtPath = null) =>
  request(`/titles/${titleId}/episodes`, {
    method: "POST",
    body: JSON.stringify({
      episode_no: episodeNo, video_path: videoPath, korean_srt_path: koreanSrtPath,
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

// Claude/GPT가 같은 세그먼트에 의견이 갈렸을 때(pending finding 두 개) 하나를
// 고른다 — 고른 쪽은 승인(final_text 있으면 그걸로 수정), 짝(otherFindingId)은
// 자동 거부된다. 승인/거부를 백엔드가 한 트랜잭션으로 처리해, 같은 세그먼트에
// 두 finding이 동시에 승인 상태로 남는 일이 없게 한다.
export const pickFinding = (findingId, otherFindingId, reviewerName, finalText = "") =>
  request(`/findings/${findingId}/pick`, {
    method: "POST",
    body: JSON.stringify({
      other_finding_id: otherFindingId, reviewer_name: reviewerName, final_text: finalText,
    }),
  });

// Claude/GPT 둘 다의 제안보다 원본이 낫다고 판단했을 때, 두 finding을 한
// 트랜잭션으로 같이 거부해 원본을 유지한다 — pickFinding과 대칭.
export const rejectFindingPair = (findingId, otherFindingId, reviewerName) =>
  request(`/findings/${findingId}/reject-pair`, {
    method: "POST",
    body: JSON.stringify({ other_finding_id: otherFindingId, reviewer_name: reviewerName }),
  });

export const requeryFinding = (findingId, instruction, reviewerName, context = "") =>
  request(`/findings/${findingId}/requery`, {
    method: "POST",
    body: JSON.stringify({ instruction, context, reviewer_name: reviewerName }),
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

// 한 줄에 성별이 다른 인물이 둘 이상일 때, 그중 한 인물(groupIndex)의
// 답만 따로 보낸다 — resolveGender와 값 종류는 같지만 대상이 줄 전체가
// 아니라 그 줄 안의 특정 인물 하나다.
export const resolveGenderGroup = (segmentId, groupIndex, gender) =>
  request(`/segments/${segmentId}/resolve-gender-group`, {
    method: "POST",
    body: JSON.stringify({ group_index: groupIndex, gender }),
  });

export const resolveFormality = (segmentId, formalityLevel) =>
  request(`/segments/${segmentId}/resolve-formality`, {
    method: "POST",
    body: JSON.stringify({ formality_level: formalityLevel }),
  });

// 겹치는 짝이 없는 반쪽짜리 Segment를 최종 자막에서 뺄지 결정한다.
export const excludeSegment = (segmentId, excluded) =>
  request(`/segments/${segmentId}/exclude`, {
    method: "POST",
    body: JSON.stringify({ excluded }),
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

export const confirmRegisters = (targetVersionId) =>
  request(`/target-versions/${targetVersionId}/confirm-registers`, { method: "POST" });

export const rerunAnalysis = (targetVersionId) =>
  request(`/target-versions/${targetVersionId}/rerun`, { method: "POST" });

export const deleteTitle = (titleId) =>
  request(`/titles/${titleId}`, { method: "DELETE" });

export const deleteTargetVersion = (targetVersionId) =>
  request(`/target-versions/${targetVersionId}`, { method: "DELETE" });

// 분석(S1) 또는 재검증(S2)이 끝날 때까지 target_version 상태를 폴링한다.
// "review"/"awaiting_confirmation"에서 멈추고(호출자가 그 status를 보고
// 어느 화면으로 갈지 정함), "failed"면 에러로 reject한다. isMounted를
// 넘기면 언마운트된 뒤에는 resolve/reject/재폴링을 하지 않는다.
export function pollTargetVersionStatus(targetVersionId, { isMounted = () => true, intervalMs = 2000 } = {}) {
  return new Promise((resolve, reject) => {
    const poll = async () => {
      if (!isMounted()) return;
      try {
        const tv = await getTargetVersion(targetVersionId);
        if (!isMounted()) return;
        if (tv.status === "review" || tv.status === "awaiting_confirmation") {
          resolve(tv.status);
        } else if (tv.status === "failed") {
          reject(new Error(tv.error_message || "처리 중 오류가 발생했습니다."));
        } else {
          setTimeout(poll, intervalMs);
        }
      } catch (err) {
        if (isMounted()) reject(err);
      }
    };
    poll();
  });
}

function sendWithProgress(path, body, onProgress, extraHeaders = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}${path}`);
    for (const [key, value] of Object.entries(extraHeaders)) {
      xhr.setRequestHeader(key, value);
    }
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
    xhr.send(body);
  });
}

function uploadWithProgress(path, file, onProgress) {
  const formData = new FormData();
  formData.append("file", file);
  return sendWithProgress(path, formData, onProgress);
}

// 영상은 multipart가 아니라 파일 그대로(raw body)로 보낸다 — FastAPI의
// 자동 multipart 파싱은 원본 전체를 먼저 통째로 임시 저장해버려서, 그
// 위에 서버가 또 복사본을 만들면 순간 필요 용량이 원본의 2배가 된다.
// 파일명은 본문이 아니라 헤더로 보낸다(한글 등 비ASCII 파일명 대응을
// 위해 encodeURIComponent). 서버는 원본을 먼저 디스크에 그대로 받아
// 적은 뒤(design 2026-09-01) ffmpeg으로 압축한다 — 그래서 원본 크기만큼의
// 여유 공간은 필요하다(2배는 아님).
function uploadRawWithProgress(path, file, onProgress) {
  return sendWithProgress(path, file, onProgress, {
    "X-Filename": encodeURIComponent(file.name),
  });
}

export const uploadVideo = (file, onProgress) =>
  uploadRawWithProgress("/uploads/video", file, onProgress);

export const uploadSrt = (file, onProgress) =>
  uploadWithProgress("/uploads/srt", file, onProgress);

export const uploadSrtKo = (file, onProgress) =>
  uploadWithProgress("/uploads/srt-ko", file, onProgress);

export const listTitles = () => request("/titles");

export const getStorageUsage = () => request("/storage");
