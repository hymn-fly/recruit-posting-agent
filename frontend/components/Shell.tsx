"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Button, Drawer } from "@heroui/react";
import { List } from "@phosphor-icons/react";
import {
  NavList,
  PageContainer,
  Sidebar,
  TopBar,
  type NavLinkRenderer,
  type NavSection,
} from "@jocoding-ax-partners/react";
import { APP_ICON, NAVIGATION } from "@/config/navigation";

/**
 * 셸의 브라우저 쪽 — 상단바 · 사이드바 · 좁은 화면 서랍 · 본문 여백.
 * 콘솔과 같은 @jocoding-ax-partners/react 의 TopBar / Sidebar / NavList / PageContainer 를 써요.
 * 로그인 정보(account)는 서버에서 읽어야 해서 AppShell.tsx 가 만들어 넘겨줘요.
 *
 * 화면을 추가할 땐 이 파일이 아니라 <AppShell> 안에 내용을 넣으세요.
 * 메뉴를 늘리려면 config/navigation.ts 를 고치세요.
 */
export function Shell({
  appName,
  account,
  isAdmin = false,
  children,
}: {
  appName: string;
  account: ReactNode;
  isAdmin?: boolean;
  children: ReactNode;
}) {
  const pathname = usePathname();
  const search = useSearchParams().toString();
  const path = pathname + (search ? `?${search}` : "");
  // 서랍을 연 화면의 주소를 기억해요. 다른 화면으로 이동하면 주소가 달라져 저절로 닫혀요.
  const [openedAt, setOpenedAt] = useState<string | null>(null);

  const sections: NavSection[] = [
    {
      key: "main",
      items: NAVIGATION.filter((item) => isAdmin || !item.adminOnly).map(
        (item) => ({
          key: item.href,
          href: item.href,
          icon: item.icon,
          label: item.label,
          active:
            item.href === "/"
              ? path === "/"
              : path === item.href || path.startsWith(`${item.href}/`),
        }),
      ),
    },
  ];
  const nav = (
    <NavList sections={sections} renderLink={renderLink} aria-label="주 메뉴" />
  );

  return (
    <div className="flex min-h-screen flex-col">
      {/* 상단바는 스크롤해도 맨 위에 붙어 있어요. */}
      <TopBar
        className="sticky top-0"
        rail={<Brand appName={appName} />}
        leading={
          <div className="flex min-w-0 items-center gap-1 lg:hidden">
            {/* 좁은 화면(1024px 미만) — 사이드바 대신 메뉴 버튼이 서랍을 열어요. */}
            <Drawer
              isOpen={openedAt === path}
              onOpenChange={(open) => setOpenedAt(open ? path : null)}
            >
              <Button
                isIconOnly
                variant="ghost"
                size="sm"
                aria-label="메뉴 열기"
              >
                <List size={20} weight="bold" />
              </Button>
              <Drawer.Backdrop>
                <Drawer.Content placement="left">
                  <Drawer.Dialog
                    aria-label="메뉴"
                    className="w-[var(--sidebar-width)] max-w-[85vw]"
                  >
                    <Drawer.Header className="flex-row items-center justify-between">
                      <Brand appName={appName} />
                      <Drawer.CloseTrigger aria-label="메뉴 닫기" />
                    </Drawer.Header>
                    <Drawer.Body>{nav}</Drawer.Body>
                    <Drawer.Footer className="justify-between">
                      {account}
                    </Drawer.Footer>
                  </Drawer.Dialog>
                </Drawer.Content>
              </Drawer.Backdrop>
            </Drawer>
            <Brand appName={appName} />
          </div>
        }
        actions={
          <div className="hidden items-center gap-3 lg:flex">{account}</div>
        }
      />

      <div className="flex flex-1">
        {/* 사이드바도 상단바 아래에 붙어 있어요. 메뉴가 길면 사이드바 안에서만 스크롤돼요. */}
        <Sidebar className="sticky top-[var(--topbar-height)] h-[calc(100vh-var(--topbar-height))]">
          {nav}
        </Sidebar>
        <main className="min-w-0 flex-1">
          {/* 바깥 여백·최대 폭의 유일한 출처. 페이지는 자기 여백을 따로 붙이지 않아요. */}
          <PageContainer>{children}</PageContainer>
        </main>
      </div>
    </div>
  );
}

function Brand({ appName }: { appName: string }) {
  return (
    <Link
      href="/"
      className="flex h-[var(--topbar-height)] min-w-0 items-center gap-2.5 px-2 text-default lg:px-5"
    >
      <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-primary text-on-primary">
        <APP_ICON size={16} weight="fill" />
      </span>
      <span className="truncate text-body-03 font-semibold">{appName}</span>
    </Link>
  );
}

/** 메뉴 링크 — 앱 안은 Next 의 Link, http(s) 주소는 새 탭. */
const renderLink: NavLinkRenderer = ({ href, children, ...rest }) =>
  /^https?:\/\//.test(href) ? (
    <a href={href} target="_blank" rel="noreferrer" {...rest}>
      {children}
    </a>
  ) : (
    <Link href={href} {...rest}>
      {children}
    </Link>
  );
