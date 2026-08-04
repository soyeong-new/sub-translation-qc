// 인물 목록을 원형으로 균등 배치하는 순수 함수. 힘-기반 그래프 레이아웃 같은
// 무거운 라이브러리 없이, 타이틀당 인물이 10~20명 수준이라는 전제로 충분한
// 단순 배치를 쓴다.

export function layoutCircle(characters, width, height, radius) {
  const cx = width / 2;
  const cy = height / 2;
  return characters.map((c, i) => {
    const angle = (2 * Math.PI * i) / characters.length - Math.PI / 2;
    return { ...c, x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) };
  });
}
