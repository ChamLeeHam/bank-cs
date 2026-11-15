function payload(method, body) {
  return { method, cache: 'no-store', ...(body === undefined ? {} : {
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }) };
}

export async function api(url, method = 'GET', body) {
  const response = await fetch(url, payload(method, body));
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请检查输入或稍后重试。');
    error.status = response.status;
    throw error;
  }
  return data;
}

export async function streamMessage(session, text, onText) {
  const response = await fetch(`/api/sessions/${session}/messages/stream`, payload('POST', { text }));
  if (!response.ok || !response.body) throw new Error('无法连接客服服务。');
  let buffer = '', answer = '';
  for await (const chunk of response.body.pipeThrough(new TextDecoderStream())) {
    buffer += chunk;
    const lines = buffer.split('\n');
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line);
      if (event.type === 'error') throw new Error(event.message);
      if (event.type === 'delta') {
        answer += event.text;
        onText(answer);
      }
      if (event.type === 'done') return { answer, pending: event.pending, sources: event.sources || [] };
    }
  }
  throw new Error('连接中断，未收到完整回复。请先查看工单或卡片状态，再决定是否重试。');
}
