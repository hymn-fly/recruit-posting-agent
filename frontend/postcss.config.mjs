// Tailwind 4 는 PostCSS 플러그인 하나로 붙어요. 설정 파일(tailwind.config.*)은 없어요 —
// 토큰·테마는 app/globals.css 의 @import 가 전부 가져와요.
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
