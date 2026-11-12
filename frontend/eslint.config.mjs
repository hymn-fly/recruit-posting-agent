// 린트 규칙 — Next.js 공식 규칙(core-web-vitals + TypeScript)을 그대로 써요.
// `npm run lint` 로 app/ · components/ · lib/ 의 .ts·.tsx 까지 검사해요.
// 규칙을 끄고 싶으면 맨 아래 객체에 rules 를 더하세요 (예: { rules: { "@next/next/no-img-element": "off" } }).
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  ...nextVitals,
  ...nextTs,
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
];

export default config;
