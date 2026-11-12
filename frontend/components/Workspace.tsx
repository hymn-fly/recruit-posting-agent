"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useSearchParams } from "next/navigation";
import {
  Button,
  Card,
  Chip,
  Input,
  Label,
  Spinner,
  TextArea,
  TextField,
} from "@heroui/react";
import {
  ArrowRight,
  ArrowUp,
  Briefcase,
  FileText,
  MagnifyingGlass,
  Plus,
  UploadSimple,
} from "@phosphor-icons/react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Shell } from "./Shell";
import { PageHeader } from "./ax";

type Job = {
  id: number;
  title: string;
  company: string;
  industry?: string;
  location?: string;
  deadline?: string;
  min_years?: number;
  url?: string;
  responsibilities?: string;
  required_qualifications?: string;
  preferred_qualifications?: string;
};
type Session = {
  session_id: string;
  candidates: Job[];
  selected_posting: Job | null;
  messages: { role: string; content: string }[];
  research_report: string;
  gap_report: string;
};
type Doc = { id: number; filename: string; kind: string; updated_at: string };
type Run = { source: string; status: string; detail: string; run_at: string };
async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : "요청을 처리하지 못했습니다.",
    );
  }
  return response.json();
}
const json = (value: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});
const kinds: Record<string, string> = {
  resume: "이력서",
  portfolio: "포트폴리오",
  career: "경력기술서",
  other: "기타",
};
function Report({ text, empty }: { text: string; empty: string }) {
  return text ? (
    <div className="report">
      <Markdown remarkPlugins={[remarkGfm]}>{text}</Markdown>
    </div>
  ) : (
    <div className="py-12 text-center text-muted">
      <FileText size={32} className="mx-auto mb-3" />
      <p>{empty}</p>
    </div>
  );
}

export function Workspace() {
  const params = useSearchParams();
  const view = params.get("view") || "jobs";
  const [session, setSession] = useState<Session | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [active, setActive] = useState(true);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [draft, setDraft] = useState("");
  const [tab, setTab] = useState("detail");
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [kind, setKind] = useState("resume");
  const [file, setFile] = useState<File | null>(null);
  const tail = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const ready = useRef(false);
  const selected = session?.selected_posting;
  const refreshDocs = useCallback(
    async () => setDocs(await api<Doc[]>("/documents")),
    [],
  );
  useEffect(() => {
    if (ready.current) return;
    ready.current = true;
    async function init() {
      try {
        const saved = localStorage.getItem("career-session");
        let s: Session;
        try {
          s = saved
            ? await api<Session>(`/sessions/${saved}`)
            : await api<Session>("/sessions", { method: "POST" });
        } catch {
          s = await api<Session>("/sessions", { method: "POST" });
        }
        localStorage.setItem("career-session", s.session_id);
        setSession(s);
        await refreshDocs();
      } catch (e) {
        setError((e as Error).message);
      }
    }
    void init();
  }, [refreshDocs]);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<{ items: Job[]; total: number }>(
      `/jobs?q=${encodeURIComponent(filter)}&active=${active}&offset=${offset}`,
    )
      .then((r) => {
        if (!cancelled) {
          setJobs(r.items);
          setTotal(r.total);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filter, active, offset]);
  useEffect(() => {
    if (view === "collection")
      void api<Run[]>("/collection")
        .then(setRuns)
        .catch((e) => setError(e.message));
  }, [view]);
  useEffect(() => {
    tail.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [session?.messages.length, busy]);
  async function action(label: string, fn: () => Promise<void>) {
    setError("");
    setNotice("");
    setBusy(label);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function send(text: string) {
    if (!session || !text.trim() || busy) return;
    const clean = text.trim();
    setDraft("");
    await action("에이전트가 요청을 처리하고 있습니다", async () => {
      const r = await api<Session>(
        `/sessions/${session.session_id}/messages`,
        json({ text: clean }),
      );
      setSession(r);
      if (r.research_report !== session.research_report) setTab("research");
      else if (r.gap_report !== session.gap_report) setTab("gap");
    });
  }
  async function select(job: Job) {
    if (!session || busy) return;
    await action("공고를 불러오는 중입니다", async () => {
      setSession(
        await api<Session>(`/sessions/${session.session_id}/select/${job.id}`, {
          method: "POST",
        }),
      );
      setTab("detail");
    });
  }
  async function upload(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    await action("문서를 읽고 프로필을 갱신하는 중입니다", async () => {
      const form = new FormData();
      form.append("file", file);
      form.append("kind", kind);
      const r = await api<{ message: string }>("/documents", {
        method: "POST",
        body: form,
      });
      await refreshDocs();
      setNotice(r.message);
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      if (session)
        setSession(await api<Session>(`/sessions/${session.session_id}`));
    });
  }
  const titles: Record<string, [string, string]> = {
    jobs: [
      "다음 커리어를 찾아보세요",
      "공고를 탐색하고, 회사의 방향과 나의 경험을 함께 살펴보세요.",
    ],
    chat: [
      "커리어 에이전트",
      "원하는 일을 이야기하면 공고 추천부터 조사와 역량 분석까지 이어집니다.",
    ],
    documents: [
      "나의 경험을 연결하세요",
      "이력서와 포트폴리오를 추가하면 실제 문서 근거로 역량을 비교합니다.",
    ],
    collection: [
      "수집 현황",
      "사이트별 최근 수집 결과와 확인이 필요한 기록을 살펴보세요.",
    ],
  };
  const heading = titles[view] || titles.jobs;
  const candidateList = session?.candidates || [];
  const jobCards = (items: Job[]) =>
    items.map((job) => (
      <Card
        key={job.id}
        className={
          selected?.id === job.id ? "outline outline-2 outline-primary" : ""
        }
      >
        <Card.Content className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3">
            <span className="t-small text-primary">{job.company}</span>
            {job.industry && <Chip size="sm">{job.industry}</Chip>}
          </div>
          <h3 className="t-card-title">{job.title}</h3>
          <p className="t-small text-muted">
            {job.location || "근무지 미기재"} ·{" "}
            {job.min_years != null
              ? `${job.min_years}년 이상`
              : "경력 조건 미기재"}
          </p>
          <div className="flex items-center justify-between gap-2">
            <span className="t-caption">
              {job.deadline ? `${job.deadline} 마감` : "마감일 미기재"}
            </span>
            <Button
              variant={selected?.id === job.id ? "primary" : "outline"}
              size="sm"
              isDisabled={!!busy || !session}
              onPress={() => void select(job)}
            >
              공고 확인 <ArrowRight />
            </Button>
          </div>
        </Card.Content>
      </Card>
    ));
  return (
    <Shell
      appName="Career Workspace"
      account={<span className="t-caption">개인 커리어 워크스페이스</span>}
    >
      <div className="flex flex-col gap-6">
        <PageHeader
          title={heading[0]}
          description={heading[1]}
          actions={
            <Button
              variant="outline"
              isDisabled={!!busy}
              onPress={() =>
                void action("새 대화를 준비합니다", async () => {
                  const s = await api<Session>("/sessions", { method: "POST" });
                  setSession(s);
                  localStorage.setItem("career-session", s.session_id);
                  setTab("detail");
                })
              }
            >
              <Plus />새 대화
            </Button>
          }
        />
        {error && (
          <div
            role="alert"
            className="rounded-card bg-danger-soft p-4 text-danger"
          >
            {error}
          </div>
        )}
        {notice && (
          <div role="status" className="rounded-card bg-primary-soft p-4">
            {notice}
          </div>
        )}
        {busy && (
          <div role="status" className="flex items-center gap-3 text-secondary">
            <Spinner size="sm" />
            {busy}
          </div>
        )}
        {view === "documents" ? (
          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <Card.Header>
                <Card.Title>문서 추가</Card.Title>
                <Card.Description>
                  PDF · DOCX · MD · TXT / 최대 20MB
                </Card.Description>
              </Card.Header>
              <Card.Content>
                <form onSubmit={upload} className="flex flex-col gap-4">
                  <label className="t-small" htmlFor="kind">
                    문서 종류
                  </label>
                  <select
                    id="kind"
                    className="rounded-input border border-default bg-content p-3"
                    value={kind}
                    onChange={(e) => setKind(e.target.value)}
                  >
                    {Object.entries(kinds).map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </select>
                  <label htmlFor="file" className="t-small">
                    파일 선택
                  </label>
                  <input
                    ref={fileInput}
                    id="file"
                    type="file"
                    accept=".pdf,.docx,.md,.txt"
                    onChange={(e) => setFile(e.target.files?.[0] || null)}
                  />
                  <p className="t-caption">
                    같은 파일명은 교체됩니다. 업로드한 모든 문서를 개인 프로필로
                    함께 사용하며, 스캔 PDF는 텍스트 변환이 필요합니다.
                  </p>
                  <Button type="submit" isDisabled={!file || !!busy}>
                    <UploadSimple />
                    업로드 및 프로필 갱신
                  </Button>
                </form>
              </Card.Content>
            </Card>
            <Card>
              <Card.Header>
                <Card.Title>분석에 사용하는 문서 · {docs.length}</Card.Title>
              </Card.Header>
              <Card.Content className="flex flex-col gap-4">
                {docs.length ? (
                  docs.map((d) => (
                    <div
                      key={d.id}
                      className="flex items-center gap-3 rounded-card bg-muted p-4"
                    >
                      <FileText size={24} />
                      <div>
                        <p className="t-small">{d.filename}</p>
                        <p className="t-caption">
                          {kinds[d.kind]} ·{" "}
                          {new Date(d.updated_at).toLocaleString("ko-KR")}
                        </p>
                      </div>
                    </div>
                  ))
                ) : (
                  <p className="text-muted">
                    등록된 문서가 없습니다. 첫 문서를 추가해 주세요.
                  </p>
                )}
              </Card.Content>
            </Card>
          </div>
        ) : view === "collection" ? (
          <Card>
            <Card.Header>
              <Card.Title>최근 수집 기록</Card.Title>
              <Card.Description>최근 50건의 수집 결과</Card.Description>
            </Card.Header>
            <Card.Content>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr>
                      <th className="p-3">사이트</th>
                      <th>결과</th>
                      <th>상세</th>
                      <th>수집 시각</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((r, i) => (
                      <tr key={i}>
                        <td className="p-3 break-all">{r.source}</td>
                        <td>
                          <Chip
                            size="sm"
                            color={
                              r.status === "ok"
                                ? "success"
                                : r.status === "failed"
                                  ? "danger"
                                  : "warning"
                            }
                          >
                            {r.status === "ok"
                              ? "정상"
                              : r.status === "failed"
                                ? "실패"
                                : "확인 불가"}
                          </Chip>
                        </td>
                        <td className="p-3">{r.detail}</td>
                        <td className="whitespace-nowrap">
                          {new Date(r.run_at).toLocaleString("ko-KR")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!runs.length && (
                  <p className="py-8 text-center text-muted">
                    수집 기록이 없습니다.
                  </p>
                )}
              </div>
            </Card.Content>
          </Card>
        ) : (
          <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
            <div className="flex min-w-0 flex-col gap-6">
              {view !== "chat" && (
                <>
                  <Card>
                    <Card.Content>
                      <form
                        className="flex flex-wrap items-end gap-3"
                        onSubmit={(e) => {
                          e.preventDefault();
                          setOffset(0);
                          setFilter(query);
                        }}
                      >
                        <TextField
                          className="min-w-0 flex-1"
                          value={query}
                          onChange={setQuery}
                        >
                          <Label>회사·직무 검색</Label>
                          <Input placeholder="회사명 또는 공고 제목" />
                        </TextField>
                        <Button type="submit" variant="secondary">
                          <MagnifyingGlass />
                          검색
                        </Button>
                        <label className="flex w-full items-center gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={active}
                            onChange={(e) => {
                              setActive(e.target.checked);
                              setOffset(0);
                            }}
                          />
                          모집 중인 공고만
                        </label>
                      </form>
                    </Card.Content>
                  </Card>
                  <div className="flex justify-between">
                    <h2 className="t-section-title">
                      채용공고 <span className="text-primary">{total}</span>
                    </h2>
                    <span className="t-caption">최신 수집 순</span>
                  </div>
                  {loading ? (
                    <Spinner />
                  ) : jobs.length ? (
                    <div className="grid gap-3">{jobCards(jobs)}</div>
                  ) : (
                    <Card>
                      <Card.Content className="py-10 text-center text-muted">
                        조건에 맞는 공고가 없습니다.
                      </Card.Content>
                    </Card>
                  )}
                  <div className="flex items-center justify-between">
                    <Button
                      size="sm"
                      variant="outline"
                      isDisabled={offset === 0}
                      onPress={() => setOffset(Math.max(0, offset - 30))}
                    >
                      이전
                    </Button>
                    <span className="t-caption">
                      {Math.floor(offset / 30) + 1} /{" "}
                      {Math.max(1, Math.ceil(total / 30))}
                    </span>
                    <Button
                      size="sm"
                      variant="outline"
                      isDisabled={offset + 30 >= total}
                      onPress={() => setOffset(offset + 30)}
                    >
                      다음
                    </Button>
                  </div>
                </>
              )}
              <Card>
                <Card.Header>
                  <Card.Title>커리어 에이전트</Card.Title>
                  <Card.Description>
                    추천된 공고를 선택하고 분석을 이어가세요.
                  </Card.Description>
                </Card.Header>
                <Card.Content className="flex flex-col gap-4">
                  <div
                    className="chat-history flex flex-col gap-4"
                    aria-live="polite"
                  >
                    {!session?.messages.length && (
                      <div className="rounded-card bg-muted p-5">
                        <p className="t-body">어떤 일을 찾고 계세요?</p>
                        <p className="mt-2 t-small text-muted">
                          예: 서울에서 LangGraph 기반 AI 에이전트를 개발하는
                          공고를 찾아줘.
                        </p>
                      </div>
                    )}
                    {session?.messages.map((m, i) => (
                      <div
                        key={i}
                        className={`rounded-card p-4 ${m.role === "user" ? "ml-6 bg-primary-soft" : "mr-2 bg-muted"}`}
                      >
                        <p className="mb-2 t-caption">
                          {m.role === "user" ? "나" : "커리어 에이전트"}
                        </p>
                        <Report text={m.content} empty="" />
                      </div>
                    ))}
                    <div ref={tail} />
                  </div>
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      void send(draft);
                    }}
                    className="flex items-end gap-2"
                  >
                    <TextField
                      className="min-w-0 flex-1"
                      value={draft}
                      onChange={setDraft}
                    >
                      <Label>에이전트에게 요청</Label>
                      <TextArea
                        placeholder="원하는 공고나 분석을 요청해 보세요"
                        rows={2}
                      />
                    </TextField>
                    <Button
                      type="submit"
                      isIconOnly
                      aria-label="메시지 보내기"
                      isDisabled={!!busy || !session || !draft.trim()}
                    >
                      <ArrowUp />
                    </Button>
                  </form>
                </Card.Content>
              </Card>
              {candidateList.length > 0 && (
                <>
                  <h2 className="t-section-title">
                    에이전트가 찾은 공고 · {candidateList.length}
                  </h2>
                  <div className="grid gap-3">{jobCards(candidateList)}</div>
                </>
              )}
            </div>
            <Card className="min-w-0 xl:sticky xl:top-20">
              <Card.Header>
                <div className="flex items-center gap-2 text-primary">
                  <Briefcase />
                  <span className="t-small">선택한 공고</span>
                </div>
                <Card.Title>
                  {selected?.title || "관심 있는 공고를 선택하세요"}
                </Card.Title>
                <Card.Description>
                  {selected
                    ? `${selected.company} · ${selected.location || "근무지 미기재"}`
                    : "공고 목록이나 에이전트 추천에서 공고 확인을 누르세요."}
                </Card.Description>
              </Card.Header>
              <Card.Content>
                {selected ? (
                  <>
                    <div
                      className="mb-5 flex flex-wrap gap-2"
                      aria-label="공고 분석"
                    >
                      {[
                        ["detail", "공고 상세"],
                        ["research", "산업·기업 조사"],
                        ["gap", "내 역량 분석"],
                      ].map(([v, l]) => (
                        <Button
                          key={v}
                          aria-pressed={tab === v}
                          size="sm"
                          variant={tab === v ? "primary" : "ghost"}
                          onPress={() => setTab(v)}
                        >
                          {l}
                        </Button>
                      ))}
                    </div>
                    {tab === "detail" ? (
                      <div className="flex flex-col gap-5">
                        <div className="flex flex-wrap gap-2">
                          <Chip>{selected.industry || "산업 미분류"}</Chip>
                          <Chip>
                            {selected.deadline
                              ? `${selected.deadline} 마감`
                              : "마감일 미기재"}
                          </Chip>
                        </div>
                        {[
                          ["하는 일", selected.responsibilities],
                          ["필수 역량", selected.required_qualifications],
                          ["우대 역량", selected.preferred_qualifications],
                        ].map(([h, t]) => (
                          <section key={h}>
                            <h3 className="mb-2 t-card-title">{h}</h3>
                            <p className="whitespace-pre-wrap t-small">
                              {t || "공고에 기재된 내용이 없습니다."}
                            </p>
                          </section>
                        ))}
                        {selected.url && /^https?:\/\//.test(selected.url) && (
                          <a
                            href={selected.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-primary underline"
                          >
                            원문 공고 열기 ↗
                          </a>
                        )}
                      </div>
                    ) : tab === "research" ? (
                      <>
                        <Button
                          variant="secondary"
                          isDisabled={!!busy}
                          onPress={() =>
                            void send(
                              "현재 선택한 공고의 산업 현황, 전망, 경쟁사와 회사를 조사해서 리포트를 작성해줘.",
                            )
                          }
                        >
                          {session.research_report
                            ? "다시 조사하기"
                            : "산업·기업 조사 시작"}
                        </Button>
                        <Report
                          text={session.research_report}
                          empty="조사를 시작하면 출처가 포함된 리포트가 여기에 표시됩니다."
                        />
                      </>
                    ) : (
                      <>
                        <p className="mb-3 t-small text-muted">
                          등록 문서 {docs.length}개를 바탕으로 요구 역량과
                          경험을 비교합니다.
                        </p>
                        <Button
                          variant="secondary"
                          isDisabled={!!busy || !docs.length}
                          onPress={() =>
                            void send(
                              "현재 선택한 공고와 내 이력서 및 포트폴리오를 비교해서 충족 역량, 부족한 점과 보완 방법을 분석해줘.",
                            )
                          }
                        >
                          내 역량 분석 시작
                        </Button>
                        {!docs.length && (
                          <p className="mt-3 t-small">
                            <a
                              href="/?view=documents"
                              className="text-primary underline"
                            >
                              이력서·포트폴리오 추가하기
                            </a>
                          </p>
                        )}
                        <Report
                          text={session.gap_report}
                          empty="분석 결과와 문서 근거, 보완 방향이 여기에 표시됩니다."
                        />
                      </>
                    )}
                  </>
                ) : (
                  <div className="py-16 text-center text-muted">
                    <Briefcase size={40} className="mx-auto mb-4" />
                    <p>공고 상세 → 산업·기업 조사 → 내 역량 분석</p>
                    <p className="mt-2 t-small">
                      하나의 공고를 기준으로 준비를 이어가세요.
                    </p>
                  </div>
                )}
              </Card.Content>
            </Card>
          </div>
        )}
      </div>
    </Shell>
  );
}
