import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { RelayError } from "../model.js";

/** Read-only app-server client. It never grants a server-initiated approval. */
export class JsonLineRpc {
  private child: ChildProcessWithoutNullStreams;
  private serial = 0;
  private buffer = "";
  private pending = new Map<
    number,
    {
      resolve: (value: unknown) => void;
      reject: (error: Error) => void;
      timer: NodeJS.Timeout;
    }
  >();
  constructor(executable: string, args: string[], cwd: string) {
    this.child = spawn(executable, args, {
      cwd,
      stdio: "pipe",
      windowsHide: true,
    });
    this.child.stderr.resume();
    this.child.on("error", () =>
      this.fail(
        new RelayError(
          "harness_unavailable",
          "Harness executable is unavailable. Configure its absolute path.",
        ),
      ),
    );
    this.child.on("exit", () =>
      this.fail(
        new RelayError("harness_exited", "Harness API process exited."),
      ),
    );
    this.child.stdin.on("error", () =>
      this.fail(new RelayError("harness_pipe", "Harness connection closed.")),
    );
    this.child.stdout.setEncoding("utf8");
    this.child.stdout.on("data", (chunk: string) => {
      this.buffer += chunk;
      if (Buffer.byteLength(this.buffer) > 16 * 1024 * 1024) {
        this.fail(
          new RelayError(
            "harness_response_too_large",
            "Harness response exceeds the capture limit.",
          ),
        );
        this.close();
        return;
      }
      let newline: number;
      while ((newline = this.buffer.indexOf("\n")) >= 0) {
        const line = this.buffer.slice(0, newline);
        this.buffer = this.buffer.slice(newline + 1);
        if (!line.trim()) continue;
        let value: {
          id?: number;
          method?: string;
          result?: unknown;
          error?: { code?: number; message?: string };
        };
        try {
          value = JSON.parse(line);
        } catch {
          this.fail(
            new RelayError("harness_protocol", "Harness emitted invalid JSON."),
          );
          this.close();
          return;
        }
        if (value.method && value.id !== undefined) {
          this.child.stdin.write(
            JSON.stringify({
              jsonrpc: "2.0",
              id: value.id,
              error: {
                code: -32601,
                message: "Relay capture does not authorize execution.",
              },
            }) + "\n",
          );
          continue;
        }
        if (value.id === undefined) continue;
        const waiter = this.pending.get(value.id);
        if (!waiter) continue;
        clearTimeout(waiter.timer);
        this.pending.delete(value.id);
        if (value.error)
          waiter.reject(
            new RelayError(
              "harness_rpc_" + String(value.error.code),
              "Harness API rejected the capture request. Check the adapter and harness versions.",
            ),
          );
        else waiter.resolve(value.result);
      }
    });
  }
  private fail(error: Error): void {
    for (const waiter of this.pending.values()) {
      clearTimeout(waiter.timer);
      waiter.reject(error);
    }
    this.pending.clear();
  }
  request<T = unknown>(method: string, params: unknown): Promise<T> {
    const id = ++this.serial;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(
          new RelayError(
            "harness_timeout",
            "Harness API did not respond within 15 seconds.",
          ),
        );
      }, 15000);
      this.pending.set(id, {
        resolve: (value) => resolve(value as T),
        reject,
        timer,
      });
      this.child.stdin.write(
        JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n",
      );
    });
  }
  notify(method: string): void {
    this.child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method }) + "\n");
  }
  close(): void {
    this.fail(new RelayError("harness_closed", "Capture connection closed."));
    this.child.stdin.end();
    this.child.kill();
  }
}
