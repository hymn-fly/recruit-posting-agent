import { Suspense } from "react";
import { Workspace } from "@/components/Workspace";
export default function Page() {
  return (
    <Suspense fallback={<p>워크스페이스를 여는 중입니다…</p>}>
      <Workspace />
    </Suspense>
  );
}
