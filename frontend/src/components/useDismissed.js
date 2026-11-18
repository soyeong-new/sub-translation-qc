import { useEffect, useState } from "react";
import { getDismissedConflicts, postDismissedConflict, deleteDismissedConflict } from "../api.js";

// 작품 단위로 "이대로 두기"한 충돌 — 서버에 저장돼 다른 회차·다른 화면에서도 유지된다.
// items: [{id, key}] (kind별), dismiss(key), restore(id).
export function useDismissed(titleId, kind, onError) {
  const [items, setItems] = useState([]);

  useEffect(() => {
    if (!titleId) return;
    let alive = true;
    getDismissedConflicts(titleId)
      .then((rows) => alive && setItems(rows.filter((r) => r.kind === kind)))
      .catch((err) => onError?.(err.message ?? "숨긴 항목을 불러오지 못했습니다."));
    return () => { alive = false; };
  }, [titleId, kind]);

  async function dismiss(key) {
    if (!titleId || items.some((i) => i.key === key)) return;
    try {
      const row = await postDismissedConflict(titleId, kind, key);
      setItems((prev) => [...prev, { id: row.id, key: row.key }]);
    } catch (err) {
      onError?.(err.message ?? "저장하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    }
  }

  async function restore(id) {
    try {
      await deleteDismissedConflict(id);
      setItems((prev) => prev.filter((i) => i.id !== id));
    } catch (err) {
      onError?.(err.message ?? "되돌리지 못했습니다. 잠시 후 다시 시도해 주세요.");
    }
  }

  return { items, keys: items.map((i) => i.key), dismiss, restore };
}
