// 인물관계도 이미지 추출 결과를 검토·수정하는 화면. 3개 컬럼(원본 이미지 /
// 편집 테이블 / 실시간 다이어그램)을 CSS grid로 배치하고, 좁은 화면에서는
// 자동으로 세로로 쌓인다. 각 편집 동작은 즉시 저장된다(기존 confirm-gender와
// 동일한 UX 관례 — 별도의 "전체 저장" 버튼 없음).

import { useEffect, useState } from "react";
import {
  getTitle, listTitleCharacters, listTitleRelationships,
  createTitleCharacter, updateCharacter, deleteCharacter,
  createTitleRelationship, updateRelationship, deleteRelationship,
  confirmChart,
} from "../api.js";
import RelationshipDiagram from "../components/RelationshipDiagram.jsx";

const inputClass =
  "block w-24 rounded-md border border-input bg-background px-2 py-1 text-sm text-foreground " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

const STATUS_LABELS = {
  none: "이미지 없음",
  processing: "추출 중...",
  review_needed: "검토 대기",
  confirmed: "검토 완료",
  failed: "추출 실패",
};

export default function ChartReviewView({ titleId, onBack }) {
  const [title, setTitle] = useState(null);
  const [characters, setCharacters] = useState([]);
  const [relationships, setRelationships] = useState([]);
  const [loadError, setLoadError] = useState(null);
  // 인물/관계/전체 확인 등 편집 동작에서 발생한 오류를 보여주는 공용 배너.
  // 이 화면은 ReviewView처럼 항목별 동시 편집이 많지 않아 단일 상태로 충분하다.
  const [actionError, setActionError] = useState(null);
  const [newCharacterLabel, setNewCharacterLabel] = useState("");
  const [newRelSpeaker, setNewRelSpeaker] = useState("");
  const [newRelAddressee, setNewRelAddressee] = useState("");
  const [newRelType, setNewRelType] = useState("");
  // 인물 이름/관계 유형 입력은 타이핑마다 저장하지 않고, blur 시점에만 저장한다.
  // 편집 중인 항목의 id와 로컬 초안 텍스트를 따로 들고 있다가, 값이 실제로
  // 바뀐 경우에만 API를 호출해 매 키 입력마다 네트워크 요청+reload가 겹치는
  // 문제를 막는다.
  const [editingCharacterId, setEditingCharacterId] = useState(null);
  const [editingLabel, setEditingLabel] = useState("");
  const [editingRelationshipId, setEditingRelationshipId] = useState(null);
  const [editingRelationshipType, setEditingRelationshipType] = useState("");

  async function reload() {
    try {
      const [t, chars, rels] = await Promise.all([
        getTitle(titleId), listTitleCharacters(titleId), listTitleRelationships(titleId),
      ]);
      setTitle(t);
      setCharacters(chars);
      setRelationships(rels);
    } catch (err) {
      setLoadError(err.message ?? "불러오지 못했습니다.");
    }
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [titleId]);

  async function handleAddCharacter(e) {
    e.preventDefault();
    if (!newCharacterLabel.trim()) return;
    try {
      await createTitleCharacter(titleId, newCharacterLabel.trim());
      setNewCharacterLabel("");
      setActionError(null);
      await reload();
    } catch (err) {
      setActionError(err.message ?? "요청 중 오류가 발생했습니다.");
    }
  }

  // 인물 이름 입력에서 focus를 잃을 때(blur)만 호출된다. 값이 원래 라벨과
  // 같으면(그냥 클릭했다가 나간 경우) 저장을 건너뛴다.
  async function handleRenameCharacterBlur(characterId, originalLabel) {
    const label = editingLabel;
    if (label === originalLabel) {
      setEditingCharacterId(null);
      return;
    }
    try {
      await updateCharacter(characterId, { label });
      setActionError(null);
      await reload();
    } catch (err) {
      setActionError(err.message ?? "요청 중 오류가 발생했습니다.");
    } finally {
      setEditingCharacterId(null);
    }
  }

  async function handleDeleteCharacter(characterId) {
    try {
      await deleteCharacter(characterId);
      setActionError(null);
      await reload();
    } catch (err) {
      setActionError(err.message ?? "요청 중 오류가 발생했습니다.");
    }
  }

  async function handleAddRelationship(e) {
    e.preventDefault();
    if (!newRelSpeaker.trim() || !newRelAddressee.trim()) return;
    try {
      await createTitleRelationship(
        titleId, newRelSpeaker.trim(), newRelAddressee.trim(), newRelType.trim() || null);
      setNewRelSpeaker("");
      setNewRelAddressee("");
      setNewRelType("");
      setActionError(null);
      await reload();
    } catch (err) {
      setActionError(err.message ?? "요청 중 오류가 발생했습니다.");
    }
  }

  // 관계 유형 입력도 blur 시점에만 저장하며, 값이 바뀌지 않았으면 건너뛴다.
  async function handleRelationshipTypeBlur(relationshipId, originalType) {
    const relationshipType = editingRelationshipType;
    if (relationshipType === originalType) {
      setEditingRelationshipId(null);
      return;
    }
    try {
      await updateRelationship(relationshipId, relationshipType);
      setActionError(null);
      await reload();
    } catch (err) {
      setActionError(err.message ?? "요청 중 오류가 발생했습니다.");
    } finally {
      setEditingRelationshipId(null);
    }
  }

  async function handleDeleteRelationship(relationshipId) {
    try {
      await deleteRelationship(relationshipId);
      setActionError(null);
      await reload();
    } catch (err) {
      setActionError(err.message ?? "요청 중 오류가 발생했습니다.");
    }
  }

  async function handleConfirm() {
    try {
      await confirmChart(titleId);
      setActionError(null);
      await reload();
    } catch (err) {
      setActionError(err.message ?? "요청 중 오류가 발생했습니다.");
    }
  }

  if (loadError) {
    return (
      <div className="min-h-screen bg-background px-6 py-8">
        <button onClick={onBack} className="text-sm text-muted-foreground hover:text-foreground">
          &larr; 목록으로
        </button>
        <p className="mt-4 text-sm text-destructive">{loadError}</p>
      </div>
    );
  }

  if (!title) {
    return <p className="p-8 text-sm text-muted-foreground">불러오는 중...</p>;
  }

  return (
    <div className="min-h-screen bg-background px-6 py-8">
      <button onClick={onBack} className="text-sm text-muted-foreground hover:text-foreground">
        &larr; 목록으로
      </button>
      <div className="mt-2 mb-6">
        <h1 className="text-xl font-semibold text-card-foreground">{title.name} — 인물관계도 검토</h1>
        <p className="text-sm text-muted-foreground">
          상태: {STATUS_LABELS[title.chart_extraction_status] ?? title.chart_extraction_status}
        </p>
        {title.chart_extraction_error && (
          <p className="text-sm text-destructive">{title.chart_extraction_error}</p>
        )}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="mb-2 text-sm font-semibold text-foreground">원본 이미지</h2>
          {title.chart_image_url ? (
            <img src={title.chart_image_url} alt="인물관계도 원본" className="w-full rounded" />
          ) : (
            <p className="text-sm text-muted-foreground">업로드된 이미지가 없습니다.</p>
          )}
        </div>

        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="mb-2 text-sm font-semibold text-foreground">편집 테이블</h2>
          {actionError && <p className="mb-2 text-sm text-destructive">{actionError}</p>}

          <h3 className="text-xs font-semibold uppercase text-muted-foreground">인물</h3>
          <div className="mt-2 flex flex-wrap gap-2">
            {characters.map((c) => (
              <div key={c.id}
                   className="flex items-center gap-1 rounded-full border border-border px-3 py-1">
                <input
                  value={editingCharacterId === c.id ? editingLabel : c.label}
                  onFocus={() => {
                    setEditingCharacterId(c.id);
                    setEditingLabel(c.label);
                  }}
                  onChange={(e) => setEditingLabel(e.target.value)}
                  onBlur={() => handleRenameCharacterBlur(c.id, c.label)}
                  className="w-16 bg-transparent text-sm text-foreground focus-visible:outline-none"
                  aria-label={`${c.label} 이름 수정`}
                />
                <button onClick={() => handleDeleteCharacter(c.id)} aria-label={`${c.label} 삭제`}
                        className="text-muted-foreground hover:text-destructive">
                  ✕
                </button>
              </div>
            ))}
          </div>
          <form onSubmit={handleAddCharacter} className="mt-2 flex gap-2">
            <input
              value={newCharacterLabel}
              onChange={(e) => setNewCharacterLabel(e.target.value)}
              placeholder="인물 이름"
              className={inputClass}
            />
            <button type="submit" className="text-sm text-primary hover:underline">+ 인물</button>
          </form>

          <h3 className="mt-4 text-xs font-semibold uppercase text-muted-foreground">관계</h3>
          <div className="mt-2 flex flex-col gap-2">
            {relationships.map((r) => (
              <div key={r.id}
                   className="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm">
                <strong className="text-foreground">{r.speaker_label}</strong>
                <span className="text-muted-foreground">—</span>
                <input
                  value={editingRelationshipId === r.id ? editingRelationshipType : (r.relationship_type ?? "")}
                  onFocus={() => {
                    setEditingRelationshipId(r.id);
                    setEditingRelationshipType(r.relationship_type ?? "");
                  }}
                  onChange={(e) => setEditingRelationshipType(e.target.value)}
                  onBlur={() => handleRelationshipTypeBlur(r.id, r.relationship_type ?? "")}
                  placeholder="관계"
                  className={inputClass}
                  aria-label="관계 유형 수정"
                />
                <span className="text-muted-foreground">→</span>
                <strong className="text-foreground">{r.addressee_label}</strong>
                <button onClick={() => handleDeleteRelationship(r.id)} aria-label="관계 삭제"
                        className="ml-auto text-muted-foreground hover:text-destructive">
                  ✕
                </button>
              </div>
            ))}
          </div>
          <form onSubmit={handleAddRelationship} className="mt-2 flex flex-wrap gap-2">
            <input value={newRelSpeaker} onChange={(e) => setNewRelSpeaker(e.target.value)}
                   placeholder="인물 A" className={inputClass} />
            <input value={newRelType} onChange={(e) => setNewRelType(e.target.value)}
                   placeholder="관계" className={inputClass} />
            <input value={newRelAddressee} onChange={(e) => setNewRelAddressee(e.target.value)}
                   placeholder="인물 B" className={inputClass} />
            <button type="submit" className="text-sm text-primary hover:underline">+ 관계</button>
          </form>

          <button
            onClick={handleConfirm}
            className="mt-4 inline-flex items-center rounded-md bg-primary px-3 py-1.5 text-sm
              font-medium text-primary-foreground hover:bg-primary/90"
          >
            검토 완료
          </button>
        </div>

        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="mb-2 text-sm font-semibold text-foreground">실시간 다이어그램</h2>
          <RelationshipDiagram characters={characters} relationships={relationships} />
        </div>
      </div>
    </div>
  );
}
