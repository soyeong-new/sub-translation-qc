/** @type {import('tailwindcss').Config} */
// 디자인 토큰 출처: ui-ux-pro-max:ui-styling 스킬 가이드(shadcn/ui HSL CSS 변수 컨벤션)를
// 적용해 이 QC 검수 도구용으로 결정한 값. 라이트 모드 우선, 장시간 검수 작업에 적합한
// 차분한 뉴트럴 배경 + 신뢰감 있는 블루 프라이머리. finding 카테고리 6종은 각각
// 색상환에서 뚜렷이 구분되는 hue를 사용해 배지로 사용 시 한눈에 분류 가능하도록 구성.
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        warning: {
          DEFAULT: "hsl(var(--warning))",
          foreground: "hsl(var(--warning-foreground))",
        },
        // Finding 카테고리별 배지 색상 (mistranslation/nuance-tone/unnatural-style/
        // locale-convention/sensitivity/formatting) — 각 카테고리는 서로 다른
        // hue를 사용해 배지만 보고도 구분 가능하도록 설계.
        finding: {
          "nuance-tone": {
            bg: "#ede9fe",
            text: "#6d28d9",
            border: "#ddd6fe",
          },
          "unnatural-style": {
            bg: "#dbeafe",
            text: "#1d4ed8",
            border: "#bfdbfe",
          },
          mistranslation: {
            bg: "#fef3c7",
            text: "#92400e",
            border: "#fde68a",
          },
          "locale-convention": {
            bg: "#ccfbf1",
            text: "#0f766e",
            border: "#99f6e4",
          },
          sensitivity: {
            bg: "#ffe4e6",
            text: "#be123c",
            border: "#fecdd3",
          },
          formatting: {
            bg: "#f1f5f9",
            text: "#334155",
            border: "#e2e8f0",
          },
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        // 한글 UI 라벨 + EN/ES 자막 원문을 함께 다루므로 한글 최적화 폰트를 우선하고
        // 라틴 문자는 Inter로 보완. Wanted Sans Variable은 index.html에서 CDN으로 로드함
        // (미로드/네트워크 실패 시 시스템 폰트로 자동 폴백).
        sans: [
          '"Wanted Sans Variable"',
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          '"Apple SD Gothic Neo"',
          '"Noto Sans KR"',
          '"Malgun Gothic"',
          "sans-serif",
        ],
        // 자막 원문/번역문 대조(EN vs ES) 시 글자 폭이 고정되어야 정렬 비교가 쉬움
        mono: [
          '"JetBrains Mono"',
          '"SFMono-Regular"',
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
