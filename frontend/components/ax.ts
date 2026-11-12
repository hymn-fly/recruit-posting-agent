"use client";

/**
 * @jocoding-ax-partners/react (axhub 콘솔 공용 컴포넌트) 는 항상 여기서 가져와요.
 *
 *   import { PageHeader, StatusDot, useConfirm } from '@/components/ax'
 *
 * 왜 이 파일을 거치나: 그 패키지는 "use client" 표시가 없어서 서버 컴포넌트(page.tsx 처럼
 * async 로 데이터를 읽는 파일)에서 바로 import 하면 빌드가 깨져요 (createContext is not a function).
 * 이 파일이 "use client" 경계라서 서버·브라우저 어느 쪽 파일에서도 그대로 쓸 수 있어요.
 * 필요한 이름이 없으면 아래 목록에 한 줄 더하세요. (`export *` 는 Next 가 막아요.)
 */
export {
  Breadcrumbs,
  CodeBlock,
  KpiStatBar,
  List,
  ListItem,
  PageHeader,
  Pagination,
  SearchBox,
  SidePanel,
  SidePanelFooter,
  SidePanelHeader,
  SkeletonRow,
  StatCard,
  StatusDot,
  TabNav,
  useConfirm,
} from "@jocoding-ax-partners/react";
export type {
  BreadcrumbEntry,
  ConfirmOptions,
  KpiStatItem,
  TabNavTab,
} from "@jocoding-ax-partners/react";
