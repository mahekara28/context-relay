import { RelayError, type RelayEvent } from "../model.js";
export const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
export const array = (value: unknown): unknown[] =>
  Array.isArray(value) ? value : [];
export const string = (value: unknown): string =>
  typeof value === "string" ? value : "";
export function visibleText(content: unknown): string {
  if (typeof content === "string") return content;
  return array(content)
    .map((part) => {
      const p = object(part);
      const type = string(p.type);
      if (["thinking", "reasoning", "redacted_thinking"].includes(type))
        return "";
      if (["text", "input_text", "output_text"].includes(type))
        return string(p.text);
      if (type === "tool_result")
        return "[tool result] " + visibleText(p.content);
      if (type === "toolResponse") {
        const result = object(p.toolResult);
        const value = result.value;
        return (
          "[tool result] " +
          (result.status === "error"
            ? string(result.error)
            : visibleText(Array.isArray(value) ? value : object(value).content))
        );
      }
      if (type === "toolRequest") {
        const call = object(object(p.toolCall).value);
        return (
          "[tool request] " +
          string(call.name) +
          " " +
          JSON.stringify(call.arguments ?? {})
        );
      }
      if (type === "tool_use" || type === "toolCall")
        return (
          "[tool request] " +
          string(p.name) +
          " " +
          JSON.stringify(p.input ?? p.arguments ?? {})
        );
      return "";
    })
    .filter(Boolean)
    .join("\n");
}
function event(
  id: string,
  role: RelayEvent["role"],
  text: string,
  kind: RelayEvent["kind"] = "message",
): RelayEvent | null {
  if (!text.trim()) return null;
  if (text.length > 100000)
    throw new RelayError(
      "event_too_large",
      "A source event exceeds 100,000 characters. Export a smaller selection.",
    );
  return { id, role, text, kind };
}
export function claudeEvents(records: unknown[]): RelayEvent[] {
  const events: RelayEvent[] = [];
  records.forEach((value, index) => {
    const record = object(value);
    const type = string(record.type);
    const message = object(record.message);
    const id = string(record.uuid) || `line-${index}`;
    if (type === "user" || type === "assistant") {
      const content = message.content;
      const body = Array.isArray(content)
        ? content.filter((part) => object(part).type !== "tool_result")
        : content;
      const result = event(id, type, visibleText(body));
      if (result) events.push(result);
      array(content).forEach((part, n) => {
        const tool = object(part);
        if (tool.type !== "tool_result") return;
        const result = event(
          id + ":tool:" + (string(tool.tool_use_id) || n),
          "tool",
          visibleText([tool]),
          "tool_result",
        );
        if (result) events.push(result);
      });
    } else if (type === "system" && record.subtype === "compact_boundary") {
      const result = event(
        id,
        "system",
        "Harness compaction boundary recorded.",
        "compaction",
      );
      if (result) events.push(result);
    }
  });
  return events;
}
export function codexEvents(turns: unknown[]): RelayEvent[] {
  const events: RelayEvent[] = [];
  for (const value of turns) {
    const turn = object(value);
    if (turn.status === "inProgress" || turn.status === "in_progress") continue;
    for (const raw of array(turn.items)) {
      const item = object(raw);
      const type = string(item.type);
      const id = `${string(turn.id)}:${string(item.id)}`;
      if (!string(turn.id) || !string(item.id))
        throw new RelayError(
          "unsupported_format",
          "Codex turn/item IDs are required for incremental capture.",
        );
      let result: RelayEvent | null = null;
      if (type === "userMessage")
        result = event(id, "user", visibleText(item.content));
      else if (type === "agentMessage")
        result = event(id, "assistant", string(item.text));
      else if (type === "commandExecution" && item.status !== "inProgress")
        result = event(
          id,
          "tool",
          `Command: ${string(item.command)}\n${string(item.aggregatedOutput)}\nExit: ${String(item.exitCode ?? "unknown")}`,
          "tool_result",
        );
      else if (type === "contextCompaction")
        result = event(
          id,
          "system",
          "Harness compaction boundary recorded.",
          "compaction",
        );
      if (result) events.push(result);
    }
  }
  return events;
}
export function openCodeEvents(messages: unknown[]): RelayEvent[] {
  const events: RelayEvent[] = [];
  for (const raw of messages) {
    const message = object(raw);
    const info = object(message.info);
    const role = string(info.role);
    if (!["user", "assistant"].includes(role)) continue;
    if (role === "assistant" && !object(info.time).completed) continue;
    if (!string(info.id))
      throw new RelayError(
        "unsupported_format",
        "OpenCode message IDs are required.",
      );
    const text = visibleText(message.parts);
    const result = event(string(info.id), role as "user" | "assistant", text);
    if (result) events.push(result);
    for (const rawPart of array(message.parts)) {
      const part = object(rawPart);
      if (part.type === "tool" && object(part.state).status === "completed") {
        const tool = event(
          `${string(info.id)}:${string(part.id) || string(part.callID)}`,
          "tool",
          string(object(part.state).output),
          "tool_result",
        );
        if (tool) events.push(tool);
      }
    }
  }
  return events;
}
export function gooseEvents(exported: unknown): RelayEvent[] {
  const value = object(exported);
  const messages = Array.isArray(value.conversation)
    ? value.conversation
    : Array.isArray(value.messages)
      ? value.messages
      : object(value.session).messages;
  if (!Array.isArray(messages))
    throw new RelayError(
      "unsupported_format",
      "Goose JSON export must contain a conversation or messages array.",
    );
  const events: RelayEvent[] = [];
  messages.forEach((raw, index) => {
    const message = object(raw);
    if (object(message.metadata).userVisible === false) return;
    const role = string(message.role);
    if (!["user", "assistant", "tool", "system"].includes(role)) return;
    const result = event(
      string(message.id) || `message-${index}`,
      (array(message.content).every(
        (part) => object(part).type === "toolResponse",
      ) && array(message.content).length
        ? "tool"
        : role) as RelayEvent["role"],
      visibleText(message.content),
      role === "tool" ||
        array(message.content).some(
          (part) => object(part).type === "toolResponse",
        )
        ? "tool_result"
        : "message",
    );
    if (result) events.push(result);
  });
  return events;
}
