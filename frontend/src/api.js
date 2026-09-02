// 백엔드 REST API를 감싸는 클라이언트 함수 모음.

const BASE = "/api";

// FastAPI 에러 응답의 detail은 보통 문자열이지만, 요청 검증 실패(422)일 때는
// [{loc, msg, type}, ...] 배열이다 — 문자열이라고 가정하고 그대로 Error
// 메시지에 넣으면 배열이 String()으로 강제변환되며 "[object Object]"로
// 깨진다(실측).
function errorMessageFrom(rawText, prefix) {
  let message = `${prefix}: ${rawText}`;
  try {
    const parsed = JSON.parse(rawText);
    if (typeof parsed?.detail === "string") {
      message = parsed.detail;
    } else if (Array.isArray(parsed?.detail)) {
      message = parsed.detail.map((d) => d.msg).join(", ");
    }
  } catch {
    // response body wasn't JSON; keep the raw-text fallback above
  }
  return message;
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const raw = await res.text();
    throw new Error(errorMessageFrom(raw, `API 오류 ${res.status}`));
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

function sendWithProgress(path, body, onProgress, extraHeaders = {}, timeoutMs = 0) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}${path}`);
    xhr.timeout = timeoutMs;
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
        reject(new Error(errorMessageFrom(xhr.responseText, `업로드 오류 ${xhr.status}`)));
      }
    };
    xhr.onerror = () => reject(new Error("업로드 중 네트워크 오류가 발생했습니다."));
    // 브라우저 탭이 백그라운드로 밀려 요청이 응답도 에러도 없이 그냥
    // 멈춰버리는 경우(실측) onload/onerror가 영영 안 불려 재시도 로직이
    // 발동할 기회조차 없다 — 타임아웃을 걸어야 그 순간 실패로 확정되고
    // 재시도가 실제로 실행된다.
    xhr.ontimeout = () => reject(new Error("업로드 요청이 시간 초과됐습니다."));
    xhr.send(body);
  });
}

function uploadWithProgress(path, file, onProgress) {
  const formData = new FormData();
  formData.append("file", file);
  return sendWithProgress(path, formData, onProgress);
}

// 영상은 청크(조각) 단위로 순서대로 올린다(design 2026-09-02) — 통짜 요청
// 하나로 보내면 연결이 한 번만 끊겨도(브라우저 탭이 백그라운드로 밀리는
// 것만으로도 실측 발생) 수백MB~수GB를 처음부터 다시 보내야 했다. 청크
// 하나가 실패하면 그 청크만 몇 번 재시도하고, 그래도 안 되면 그때 실패
// 처리한다. File.slice()는 브라우저 기본 기능이라 추가 라이브러리 없이도
// 나눌 수 있다.
const VIDEO_CHUNK_SIZE = 20 * 1024 * 1024; // 20MB
const VIDEO_CHUNK_RETRIES = 3;
const VIDEO_CHUNK_TIMEOUT_MS = 90 * 1000; // 20MB면 넉넉한 여유(느린 회선 포함)

async function sendChunkWithRetry(chunk, headers) {
  for (let attempt = 1; attempt <= VIDEO_CHUNK_RETRIES; attempt++) {
    try {
      return await sendWithProgress("/uploads/video/chunk", chunk, null, headers, VIDEO_CHUNK_TIMEOUT_MS);
    } catch (err) {
      if (attempt === VIDEO_CHUNK_RETRIES) throw err;
    }
  }
}

export async function uploadVideo(file, onProgress) {
  const totalChunks = Math.max(1, Math.ceil(file.size / VIDEO_CHUNK_SIZE));
  const uploadId = crypto.randomUUID();
  const filename = encodeURIComponent(file.name);
  let result;
  try {
    for (let i = 0; i < totalChunks; i++) {
      const chunk = file.slice(i * VIDEO_CHUNK_SIZE, (i + 1) * VIDEO_CHUNK_SIZE);
      result = await sendChunkWithRetry(chunk, {
        "X-Filename": filename,
        "X-Upload-Id": uploadId,
        "X-Chunk-Index": String(i),
        "X-Total-Chunks": String(totalChunks),
        "X-Total-Size": String(file.size),
      });
      if (onProgress) onProgress(Math.round(((i + 1) / totalChunks) * 100));
    }
  } catch (err) {
    // 재시도까지 다 실패 — 서버는 이 실패를 알 방법이 없어 세션과 이어
    // 붙이던 임시 원본 파일이 디스크에 그대로 남는다(실측). 정리를
    // 요청하되, 실패해도 업로드 실패 자체를 가리면 안 되니 조용히 무시한다.
    fetch(`${BASE}/uploads/video/chunk/${uploadId}`, { method: "DELETE" }).catch(() => {});
    throw err;
  }
  return result;
}

export const uploadSrt = (file, onProgress) =>
  uploadWithProgress("/uploads/srt", file, onProgress);

export const uploadSrtKo = (file, onProgress) =>
  uploadWithProgress("/uploads/srt-ko", file, onProgress);

export const listTitles = () => request("/titles");

export const getStorageUsage = () => request("/storage");
