// tink-inject for Pi: hands Pi's events to `tink-inject hook`. It only ever adds context and never blocks a tool.
// Load with `pi -e /path/to/tink-route/integrations/pi/inject.ts` (needs `tink-inject` on PATH).
import { execFileSync } from "node:child_process";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// Degradation is for the operator, once per message: never part of what the agent sees.
const warned = new Set<string>();
function warn(message: string) {
  if (warned.has(message)) return;
  warned.add(message);
  console.error(message);
}

function inject(event: Record<string, unknown>): string | undefined {
  try {
    const out = execFileSync("tink-inject", ["hook"], {
      input: JSON.stringify(event), encoding: "utf8", timeout: 120_000, stdio: ["pipe", "pipe", "ignore"],
    });
    if (!out.trim()) return undefined;
    const result = JSON.parse(out);
    if (result.systemMessage) warn(result.systemMessage);
    return result.hookSpecificOutput?.additionalContext;
  } catch (error: any) {
    // Fail open for the agent, but say so: a missing `tink-inject` or a timeout must not be silent.
    warn(`tink-inject: guidance unavailable (${error?.code ?? error?.signal ?? "error"}); the agent continues without it.`);
    return undefined;
  }
}

const TOOLS: Record<string, string> = { bash: "Bash", write: "Write", edit: "Edit", read: "Read" };

export default function injectPi(pi: ExtensionAPI) {
  pi.on("before_agent_start", async (event, ctx) => {
    const text = inject({ hook_event_name: "UserPromptSubmit", prompt: event.prompt, cwd: ctx.cwd, session_id: ctx.sessionManager.getSessionId() });
    if (text) return { message: { customType: "guidance", content: text, display: false } };
  });

  pi.on("tool_result", async (event, ctx) => {
    const tool = TOOLS[event.toolName];
    if (!tool) return;
    const output = event.content.map((c: any) => (c.type === "text" ? c.text : "")).join("\n");
    const text = inject({
      hook_event_name: event.isError ? "PostToolUseFailure" : "PostToolUse",
      tool_name: tool,
      tool_input: event.input,
      tool_response: { stdout: output },
      error: event.isError ? output : undefined,
      cwd: ctx.cwd,
      session_id: ctx.sessionManager.getSessionId(),
    });
    if (text) return { content: [...event.content, { type: "text", text }] };
  });
}
