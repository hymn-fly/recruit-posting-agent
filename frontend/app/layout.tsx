import type { Metadata } from "next";
import { Providers } from "@/components/Providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "Career Workspace",
  description: "공고 탐색부터 산업 조사와 내 역량 분석까지",
};

// 다크 모드 — 그리기 전에 시스템 설정을 보고 <html data-theme="dark"> 를 붙여요 (깜박임 방지).
// 디자인 시스템은 이 속성만 보고 색을 바꿔요. 직접 토글을 만들면 이 속성만 바꾸면 돼요.
const THEME_SCRIPT = `try{if(matchMedia('(prefers-color-scheme: dark)').matches)document.documentElement.dataset.theme='dark'}catch(e){}`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // suppressHydrationWarning — 위 스크립트가 data-theme 를 먼저 붙여서 생기는 차이는 정상이에요.
    <html lang="ko" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
        {/* Pretendard — axhub 디자인 시스템 기본 폰트 (CDN) */}
        <link rel="preconnect" href="https://cdn.jsdelivr.net" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css"
        />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
