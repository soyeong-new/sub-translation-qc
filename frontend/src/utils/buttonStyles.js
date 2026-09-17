// ReviewView와 FlaggedSegmentStepper가 각자 따로 정의하던 버튼 기본 스타일을
// 여기 하나로 모은다 — 따로 두면 화면마다 모서리 둥글기/글자 크기가 조금씩
// 어긋나기 쉽다(실제로 rounded-md/lg, text-xs/sm으로 갈라져 있었음).
export const btnBase =
  "inline-flex items-center justify-center gap-1.5 rounded-lg px-2.5 py-1 text-sm font-medium " +
  "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50";
