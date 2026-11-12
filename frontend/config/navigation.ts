"use client";
import {
  Briefcase,
  ChatCircleDots,
  Files,
  ChartBar,
  type Icon,
} from "@phosphor-icons/react";
export const APP_ICON: Icon = Briefcase;
export type NavItem = {
  label: string;
  href: string;
  icon: Icon;
  adminOnly?: boolean;
};
export const NAVIGATION: NavItem[] = [
  { label: "공고 탐색", href: "/", icon: Briefcase },
  { label: "에이전트 채팅", href: "/?view=chat", icon: ChatCircleDots },
  { label: "내 문서", href: "/?view=documents", icon: Files },
  { label: "수집 현황", href: "/?view=collection", icon: ChartBar },
];
