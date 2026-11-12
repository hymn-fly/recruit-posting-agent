"use client";

import type { ReactNode } from "react";
import { I18nProvider, Toast } from "@heroui/react";
import { ConfirmProvider } from "@jocoding-ax-partners/react";
import { IconContext } from "@phosphor-icons/react";

/**
 * 앱 전체를 한 번 감싸는 브라우저 쪽 설정 (app/layout.tsx 가 써요). axhub 콘솔과 같은 구성이에요.
 *   - IconContext   아이콘 기본 굵기·크기 (콘솔: fill · 16)
 *   - I18nProvider  HeroUI 의 접근성 문구(스크린리더)를 한국어로 고정
 *   - ConfirmProvider  useConfirm() 확인창 — window.confirm 대신 써요
 *   - Toast.Provider   toast.success('저장했어요') 가 뜨는 자리 (오른쪽 위)
 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <IconContext.Provider value={{ weight: "fill", size: 16 }}>
      <I18nProvider locale="ko-KR">
        <ConfirmProvider>{children}</ConfirmProvider>
        <Toast.Provider placement="top end" />
      </I18nProvider>
    </IconContext.Provider>
  );
}
