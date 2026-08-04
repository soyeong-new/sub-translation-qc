// 인물/관계 목록을 원형 배치 SVG로 그리는 읽기 전용 다이어그램. 편집은 옆의
// 테이블에서 하고, 이 컴포넌트는 그 결과를 즉시 시각화하는 역할만 한다.

import { layoutCircle } from "../utils/circleLayout.js";

const WIDTH = 280;
const HEIGHT = 280;
const NODE_RADIUS = 22;

export default function RelationshipDiagram({ characters, relationships }) {
  if (characters.length === 0) {
    return <p className="text-sm text-muted-foreground">표시할 인물이 없습니다.</p>;
  }

  const layoutRadius = Math.min(WIDTH, HEIGHT) / 2 - 40;
  const positioned = layoutCircle(characters, WIDTH, HEIGHT, layoutRadius);
  const byId = Object.fromEntries(positioned.map((c) => [c.id, c]));

  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" style={{ color: "#8a8" }}>
      <defs>
        <marker id="chart-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
          <path d="M0,0 L0,6 L7,3 z" fill="currentColor" />
        </marker>
      </defs>
      {relationships.map((r) => {
        const from = byId[r.speaker_character_id];
        const to = byId[r.addressee_character_id];
        if (!from || !to) return null;
        return (
          <g key={r.id}>
            <line x1={from.x} y1={from.y} x2={to.x} y2={to.y}
                  stroke="currentColor" markerEnd="url(#chart-arrow)" />
            {r.relationship_type && (
              <text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 - 4}
                    textAnchor="middle" fontSize="9" fill="currentColor">
                {r.relationship_type}
              </text>
            )}
          </g>
        );
      })}
      {positioned.map((c) => (
        <g key={c.id}>
          <circle cx={c.x} cy={c.y} r={NODE_RADIUS} fill="#2a2a2a" stroke="currentColor" />
          <text x={c.x} y={c.y + 4} textAnchor="middle" fontSize="11" fill="#eee">
            {c.label}
          </text>
        </g>
      ))}
    </svg>
  );
}
