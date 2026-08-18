/**
 * OpenFOAM agent harness adapter for Pi.
 *
 * Starts the Python harness (src/harness/mcp_server.py) as a child process,
 * speaks newline-delimited JSON-RPC 2.0 to it, and republishes every harness
 * tool as a native Pi tool. Python stays the single source of truth: no tool
 * logic is duplicated here.
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { createInterface, type Interface } from "node:readline";
import { fileURLToPath } from "node:url";

const HARNESS_MODULE = "harness.mcp_server";
const START_TIMEOUT_MS = 30_000;
const CALL_TIMEOUT_MS = 900_000;

// ---------------------------------------------------------------------------
// Repository / interpreter / workspace resolution
// ---------------------------------------------------------------------------

function moduleDir(): string {
  try {
    // ESM
    return dirname(fileURLToPath(import.meta.url));
  } catch {
    /* fall through */
  }
  try {
    // CJS (jiti)
    return __dirname;
  } catch {
    return process.cwd();
  }
}

function findRepoRoot(): string | undefined {
  const fromEnv = process.env.OPENFOAM_HARNESS_REPO;
  if (fromEnv && existsSync(join(fromEnv, "src", "harness", "mcp_server.py"))) {
    return resolve(fromEnv);
  }
  const candidates = [moduleDir(), process.cwd()];
  for (const start of candidates) {
    let dir = resolve(start);
    for (let depth = 0; depth < 8; depth++) {
      if (existsSync(join(dir, "src", "harness", "mcp_server.py"))) return dir;
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  }
  return undefined;
}

function resolvePython(repoRoot: string): string {
  const fromEnv = process.env.OPENFOAM_HARNESS_PYTHON;
  if (fromEnv) return fromEnv;
  const venv = join(repoRoot, ".venv", "bin", "python3");
  if (existsSync(venv)) return venv;
  const venvWin = join(repoRoot, ".venv", "Scripts", "python.exe");
  if (existsSync(venvWin)) return venvWin;
  return "python3";
}

function resolveWorkspace(repoRoot: string): string {
  const dir = resolve(process.env.OPENFOAM_WORKSPACE_DIR || join(repoRoot, "workspace"));
  mkdirSync(dir, { recursive: true });
  return dir;
}

// ---------------------------------------------------------------------------
// JSON-RPC client over the Python child process
// ---------------------------------------------------------------------------

interface RpcResponse {
  id?: number | string | null;
  result?: unknown;
  error?: { code: number; message: string };
}

interface Pending {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
}

interface HarnessToolDefinition {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

class PythonHarness {
  private child?: ChildProcessWithoutNullStreams;
  private reader?: Interface;
  private pending = new Map<number, Pending>();
  private nextId = 1;
  private stderrTail: string[] = [];
  private starting?: Promise<void>;

  readonly python: string;
  readonly repoRoot: string;
  readonly workspaceDir: string;

  constructor(python: string, repoRoot: string, workspaceDir: string) {
    this.python = python;
    this.repoRoot = repoRoot;
    this.workspaceDir = workspaceDir;
  }

  get running(): boolean {
    return Boolean(this.child && this.child.exitCode === null && !this.child.killed);
  }

  /** Start the child process and complete the JSON-RPC initialize handshake. */
  async start(): Promise<void> {
    if (this.running) return;
    if (this.starting) return this.starting;
    this.starting = this.doStart().finally(() => {
      this.starting = undefined;
    });
    return this.starting;
  }

  private async doStart(): Promise<void> {
    const srcDir = join(this.repoRoot, "src");
    const existingPath = process.env.PYTHONPATH;
    const child = spawn(
      this.python,
      ["-u", "-m", HARNESS_MODULE, "--workspace-dir", this.workspaceDir],
      {
        cwd: this.repoRoot,
        env: {
          ...process.env,
          PYTHONPATH: existingPath ? `${srcDir}:${existingPath}` : srcDir,
          PYTHONUNBUFFERED: "1",
        },
        stdio: ["pipe", "pipe", "pipe"],
      },
    ) as ChildProcessWithoutNullStreams;

    this.child = child;
    this.stderrTail = [];

    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk: string) => {
      this.stderrTail.push(chunk);
      if (this.stderrTail.length > 40) this.stderrTail.shift();
    });

    this.reader = createInterface({ input: child.stdout });
    this.reader.on("line", (line) => this.onLine(line));

    child.on("exit", (code, signal) => {
      const detail = this.stderrTail.join("").trim();
      const reason = `OpenFOAM harness process exited (code ${code ?? "null"}, signal ${signal ?? "none"}).${
        detail ? `\n${detail}` : ""
      }`;
      this.failAllPending(new Error(reason));
      this.child = undefined;
    });

    child.on("error", (error) => {
      this.failAllPending(new Error(`Could not start the OpenFOAM harness: ${error.message}`));
    });

    await this.request(
      "initialize",
      { protocolVersion: "2024-11-05", clientInfo: { name: "pi", version: "1" }, capabilities: {} },
      START_TIMEOUT_MS,
    );
  }

  private onLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed) return;
    let message: RpcResponse;
    try {
      message = JSON.parse(trimmed) as RpcResponse;
    } catch {
      // Not JSON: harness diagnostics leaking onto stdout. Ignore but keep for errors.
      this.stderrTail.push(`${trimmed}\n`);
      if (this.stderrTail.length > 40) this.stderrTail.shift();
      return;
    }
    if (typeof message.id !== "number") return;
    const pending = this.pending.get(message.id);
    if (!pending) return;
    this.pending.delete(message.id);
    clearTimeout(pending.timer);
    if (message.error) {
      pending.reject(new Error(message.error.message || "OpenFOAM harness returned an error."));
      return;
    }
    pending.resolve(message.result);
  }

  private failAllPending(error: Error): void {
    for (const [, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  async request(method: string, params: unknown, timeoutMs = CALL_TIMEOUT_MS): Promise<unknown> {
    const child = this.child;
    if (!child || child.exitCode !== null) {
      throw new Error("The OpenFOAM harness is not running.");
    }
    const id = this.nextId++;
    const payload = `${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`;
    return new Promise((resolvePromise, rejectPromise) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        rejectPromise(new Error(`The OpenFOAM harness did not answer '${method}' in time.`));
      }, timeoutMs);
      this.pending.set(id, { resolve: resolvePromise, reject: rejectPromise, timer });
      child.stdin.write(payload, (error) => {
        if (!error) return;
        this.pending.delete(id);
        clearTimeout(timer);
        rejectPromise(new Error(`Could not talk to the OpenFOAM harness: ${error.message}`));
      });
    });
  }

  async listTools(): Promise<HarnessToolDefinition[]> {
    const result = (await this.request("tools/list", {}, START_TIMEOUT_MS)) as {
      tools?: HarnessToolDefinition[];
    };
    return result?.tools ?? [];
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<{ text: string; isError: boolean }> {
    const result = (await this.request("tools/call", { name, arguments: args })) as {
      content?: Array<{ type: string; text?: string }>;
      isError?: boolean;
    };
    const text = (result?.content ?? [])
      .filter((part) => part.type === "text" && typeof part.text === "string")
      .map((part) => part.text as string)
      .join("\n");
    return { text: text || "(no output)", isError: Boolean(result?.isError) };
  }

  stop(): void {
    this.failAllPending(new Error("The OpenFOAM harness was shut down."));
    this.reader?.close();
    this.reader = undefined;
    const child = this.child;
    this.child = undefined;
    if (!child) return;
    try {
      child.stdin.end();
    } catch {
      /* ignore */
    }
    child.kill("SIGTERM");
    setTimeout(() => {
      if (child.exitCode === null && !child.killed) child.kill("SIGKILL");
    }, 2000).unref?.();
  }
}

// ---------------------------------------------------------------------------
// Tool discovery cache (avoids a second process spawn per session)
// ---------------------------------------------------------------------------

/**
 * Fetch tool definitions using a short-lived process. The long-lived process is
 * only started when a tool is actually called, per pi extension guidance.
 */
async function discoverTools(harness: PythonHarness): Promise<HarnessToolDefinition[]> {
  const probe = new PythonHarness(harness.python, harness.repoRoot, harness.workspaceDir);
  try {
    await probe.start();
    return await probe.listTools();
  } finally {
    probe.stop();
  }
}

function schemaFor(tool: HarnessToolDefinition): Record<string, unknown> {
  const schema = tool.inputSchema && typeof tool.inputSchema === "object" ? { ...tool.inputSchema } : {};
  if (!schema.type) schema.type = "object";
  if (!schema.properties) schema.properties = {};
  return schema;
}

/**
 * Always-in-context guardrails.
 *
 * The skill carries the full workflow, but skills load on demand — if the model
 * never reads it, it falls back to its training data, which is overwhelmingly
 * OpenFOAM 9 / ESI. That reliably produces `application icoFoam;` and
 * `transportProperties`, neither of which exists in v13. These bullets are
 * injected into the system prompt whenever the tool is active, so the single
 * highest-frequency error is corrected without requiring a skill read.
 */
const PROMPT_GUIDELINES: Record<string, string[]> = {
  write_case_file: [
    "When using write_case_file, follow OpenFOAM v13 (openfoam.org) conventions, not OpenFOAM 9 or ESI ones: system/controlDict selects the solver with a top-level `solver` entry naming the module that matches the case physics (call list_case_types for the valid names) and never an `application` entry, and physical properties live in constant/physicalProperties and constant/momentumTransport, never constant/transportProperties or constant/turbulenceProperties.",
  ],
  scaffold_case: [
    "Prefer scaffold_case over hand-writing OpenFOAM dictionaries; it emits correct v13 files, whereas recalled OpenFOAM syntax is usually the older v9 convention.",
  ],
  run_preflight_audit: [
    "Preflight findings are static evidence, not verdicts. If a case demonstrably runs in the solver and its residuals fall, believe the run over the audit and report the finding as a likely false positive; never rewrite a working configuration just to satisfy a checker. A PASS is equally not a promise of convergence.",
  ],
  run_bounded_dry_run: [
    "Diagnose a bounded dry run by residual pattern, not by the pass/fail word: a NaN in the first solve points at initial fields or relaxation; residuals rising from step 1 point at the time-step or scheme; one field stalling while the others decay points at that equation's solver settings, relaxation and boundary conditions. The field named in a NaN message is where to start looking, not necessarily where the fault is.",
  ],
};

function titleCase(name: string): string {
  return name
    .split("_")
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(" ");
}

// ---------------------------------------------------------------------------
// Extension entry point
// ---------------------------------------------------------------------------

export default async function (pi: ExtensionAPI) {
  const repoRoot = findRepoRoot();
  if (!repoRoot) {
    pi.on("session_start", async (_event, ctx: ExtensionContext) => {
      ctx.ui.notify(
        "OpenFOAM tools are unavailable: the harness repository could not be located. Run ./setup.sh from the project folder.",
        "error",
      );
    });
    return;
  }

  const workspaceDir = resolveWorkspace(repoRoot);
  const python = resolvePython(repoRoot);
  const harness = new PythonHarness(python, repoRoot, workspaceDir);

  let definitions: HarnessToolDefinition[] = [];
  let discoveryError: string | undefined;
  try {
    definitions = await discoverTools(harness);
  } catch (error) {
    discoveryError = error instanceof Error ? error.message : String(error);
  }

  if (discoveryError || definitions.length === 0) {
    pi.on("session_start", async (_event, ctx: ExtensionContext) => {
      ctx.ui.notify(
        `OpenFOAM tools could not be loaded. Run ./setup.sh again.\n${discoveryError ?? "No tools were reported by the harness."}`,
        "error",
      );
    });
    return;
  }

  for (const definition of definitions) {
    pi.registerTool({
      name: definition.name,
      label: titleCase(definition.name),
      description: definition.description ?? definition.name,
      promptGuidelines: PROMPT_GUIDELINES[definition.name],
      // The harness publishes JSON Schema, which is the same wire shape typebox emits.
      parameters: schemaFor(definition) as never,
      async execute(_toolCallId, params, signal, onUpdate) {
        if (signal?.aborted) {
          return { content: [{ type: "text", text: "Cancelled." }], details: {} };
        }
        if (!harness.running) {
          onUpdate?.({ content: [{ type: "text", text: "Starting the OpenFOAM harness..." }] });
          try {
            await harness.start();
          } catch (error) {
            const detail = error instanceof Error ? error.message : String(error);
            throw new Error(
              `The OpenFOAM tools could not start.\n${detail}\n\nTry running ./setup.sh again from ${repoRoot}.`,
            );
          }
        }
        const { text, isError } = await harness.callTool(
          definition.name,
          (params ?? {}) as Record<string, unknown>,
        );
        if (isError) throw new Error(text);
        return { content: [{ type: "text", text }], details: { workspaceDir } };
      },
    });
  }

  pi.on("session_start", async (_event, ctx: ExtensionContext) => {
    ctx.ui.setStatus("openfoam", `OpenFOAM tools ready (${definitions.length}) · workspace/`);
  });

  pi.on("session_shutdown", async () => {
    harness.stop();
  });
}
