// POST 요청으로 여는 SSE 스트림 클라이언트. EventSource는 GET만 지원해서 fetch + ReadableStream으로 읽는다.

export type ServerSentEvent = { event: string; data: unknown };

/**
 * 지금까지 받은 텍스트(buffer)에서 "완성된" SSE 이벤트들을 꺼내고, 아직 덜 온 꼬리(rest)를 돌려준다.
 *
 * 서버가 보내는 형식 (이벤트 하나 = 빈 줄로 끝나는 블록):
 *   event: step
 *   data: {"agent":"job_search","status":"start", ...}
 *   <빈 줄>
 *
 *   : keepalive      <- ':'로 시작하는 줄은 하트비트 주석. 이벤트가 아니므로 무시
 *   <빈 줄>
 *
 * 네트워크 청크는 이벤트 경계와 상관없이 잘려서 온다. 예를 들어 한 번에 "event: step\ndata: {"ag" 까지만
 * 올 수 있고, 이때 그 블록은 아직 완성이 아니니 events에 넣지 말고 rest로 돌려줘야 다음 청크와 이어 붙일 수 있다.
 */
export function parseServerSentEvents(buffer: string): {
  events: ServerSentEvent[];
  rest: string;
} {
  // 빈 줄(\n\n)로 나누면 마지막 조각은 항상 "아직 끝나지 않은 꼬리"다. (버퍼가 \n\n으로 끝났으면 빈 문자열)
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";

  // 나머지 조각들은 빈 줄까지 도착한 완성된 블록이다
  const events: ServerSentEvent[] = [];
  for (const block of blocks) {
    const lines = block.split("\n");
    const eventLine = lines.find((line) => line.startsWith("event:"));
    const dataLine = lines.find((line) => line.startsWith("data:"));

    // data 줄이 없는 블록은 하트비트(": keepalive") 같은 주석이다
    if (!dataLine) continue;

    try {
      events.push({
        event: eventLine ? eventLine.slice("event:".length).trim() : "message",
        data: JSON.parse(dataLine.slice("data:".length).trim()),
      });
    } catch {
      // 깨진 JSON 이벤트 하나 때문에 스트림 전체가 죽지 않게 그 이벤트만 건너뛴다
      continue;
    }
  }

  return { events, rest };
}

/**
 * path로 POST 요청을 보내고, 서버가 보내는 SSE 이벤트를 도착하는 대로 onEvent에 넘긴다.
 * 스트림이 끝나면 resolve, HTTP 에러나 네트워크 에러면 reject. signal로 중간에 끊을 수 있다.
 */
export async function streamServerSentEvents(
  path: string,
  options: RequestInit,
  onEvent: (event: ServerSentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api${path}`, { ...options, signal });

  // 스트림이 열리기 전의 실패(404 등)는 일반 JSON 에러 응답이다
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null);
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : "요청을 처리하지 못했습니다.",
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let unparsedText = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    // stream: true로 청크 경계에서 잘린 한글(멀티바이트) 문자를 다음 청크와 이어서 해석한다
    unparsedText += decoder.decode(value, { stream: true });
    const { events, rest } = parseServerSentEvents(unparsedText);
    unparsedText = rest;
    for (const event of events) onEvent(event);
  }
}
