#!/usr/bin/env node
/**
 * Deterministic regression test for the Pi adapter extension.
 *
 * Drives integrations/pi/extensions/openfoam-tools.ts with a stub ExtensionAPI
 * and the real Python harness. Requires Node with TypeScript type stripping
 * (Node >= 22.6 with --experimental-strip-types, or Node >= 23 by default).
 *
 * Run: node tests/pi_adapter/adapter_test.mjs
 */

import assert from "node:assert/strict";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const EXTENSION = join(REPO_ROOT, "integrations", "pi", "extensions", "openfoam-tools.ts");
const EXPECTED_TOOL_COUNT = 19;

const failures = [];
const passes = [];

function check(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => passes.push(name))
    .catch((error) => failures.push(`${name}: ${error?.message ?? error}`));
}

/** Minimal stand-in for pi's ExtensionAPI. */
function createStubPi() {
  const tools = new Map();
  const handlers = new Map();
  return {
    tools,
    handlers,
    registerTool(definition) {
      tools.set(definition.name, definition);
    },
    registerCommand() {},
    on(event, handler) {
      const list = handlers.get(event) ?? [];
      list.push(handler);
      handlers.set(event, list);
    },
    async emit(event, payload = {}) {
      for (const handler of handlers.get(event) ?? []) {
        await handler(payload, stubContext);
      }
    },
  };
}

const notifications = [];
const stubContext = {
  cwd: REPO_ROOT,
  ui: {
    notify: (message, level) => notifications.push({ message, level }),
    setStatus: () => {},
    setWidget: () => {},
    confirm: async () => true,
  },
};

async function main() {
  assert.ok(existsSync(EXTENSION), `extension missing: ${EXTENSION}`);

  const workspace = mkdtempSync(join(tmpdir(), "openfoam-adapter-"));
  process.env.OPENFOAM_HARNESS_REPO = REPO_ROOT;
  process.env.OPENFOAM_WORKSPACE_DIR = workspace;

  const venvPython = join(REPO_ROOT, ".venv", "bin", "python3");
  if (existsSync(venvPython)) process.env.OPENFOAM_HARNESS_PYTHON = venvPython;

  const module = await import(pathToFileURL(EXTENSION).href);
  const factory = module.default;
  assert.equal(typeof factory, "function", "extension must export a default factory");

  const pi = createStubPi();

  // 1. starts the python server, initializes, and registers every harness tool
  await check("starts the harness and registers all tools", async () => {
    await factory(pi);
    assert.equal(
      pi.tools.size,
      EXPECTED_TOOL_COUNT,
      `expected ${EXPECTED_TOOL_COUNT} tools, got ${pi.tools.size} (notifications: ${JSON.stringify(notifications)})`,
    );
  });

  // 2. tool schemas are usable object schemas
  await check("tool schemas are object schemas", () => {
    for (const [name, tool] of pi.tools) {
      assert.equal(tool.parameters.type, "object", `${name} schema is not an object schema`);
      assert.ok(typeof tool.description === "string" && tool.description.length > 0, `${name} has no description`);
      assert.equal(typeof tool.execute, "function", `${name} has no execute`);
    }
  });

  // 3. v13 guardrails reach the system prompt without requiring a skill read
  await check("injects v13 prompt guidelines", () => {
    const guidelines = (pi.tools.get("write_case_file")?.promptGuidelines ?? []).join(" ");
    assert.match(guidelines, /top-level `solver` entry/, "missing the v13 solver-entry guideline");
    assert.doesNotMatch(
      guidelines,
      /`solver incompressibleFluid;`/,
      "guideline hardcodes one module; models copy it literally into VoF/buoyant cases",
    );
    assert.match(guidelines, /never an `application` entry/, "missing the application warning");
    assert.match(guidelines, /physicalProperties/, "missing the physicalProperties guideline");
  });

  // 3b. a false FAIL from our own audit once hid the working RHVT config, so the
  // "evidence, not verdict" rule must be in the system prompt, not only in the skill
  await check("injects preflight-scepticism guidelines", () => {
    const audit = (pi.tools.get("run_preflight_audit")?.promptGuidelines ?? []).join(" ");
    assert.match(audit, /not verdicts/, "audit findings not framed as evidence");
    assert.match(audit, /false positive/, "no false-positive guidance");
    assert.match(audit, /never rewrite a working configuration/i, "no protection for a working case");

    const dryRun = (pi.tools.get("run_bounded_dry_run")?.promptGuidelines ?? []).join(" ");
    assert.match(dryRun, /residual pattern/, "no residual-pattern triage guidance");
    assert.doesNotMatch(
      dryRun,
      /rho 0\.05|relaxation of 0\./,
      "guideline hardcodes a tuning value; models copy it into unrelated cases",
    );
  });

  // 4. a harmless tool call round-trips through the real Python harness
  await check("calls list_case_types through the harness", async () => {
    const tool = pi.tools.get("list_case_types");
    assert.ok(tool, "list_case_types was not registered");
    const result = await tool.execute("test-call-1", {}, undefined, undefined, stubContext);
    const text = result.content.map((part) => part.text).join("");
    const payload = JSON.parse(text);
    assert.ok(payload.case_types ?? payload.caseTypes ?? payload, "no case types in the response");
    assert.ok(text.includes("incompressibleFluid"), "expected incompressibleFluid in the tool output");
  });

  // 4. the workspace passed to the harness is the dedicated one
  await check("passes the dedicated workspace path", async () => {
    const tool = pi.tools.get("scaffold_case");
    assert.ok(tool, "scaffold_case was not registered");
    const result = await tool.execute(
      "test-call-2",
      { case_name: "adapter_probe", box_size: [0.1, 0.1, 0.01], cells: [5, 5, 1] },
      undefined,
      undefined,
      stubContext,
    );
    const text = result.content.map((part) => part.text).join("");
    assert.ok(text.includes(workspace), `case was not created inside the dedicated workspace:\n${text}`);
  });

  // 5. tool errors surface as thrown errors, not silent successes
  await check("surfaces harness errors", async () => {
    const tool = pi.tools.get("read_case_file");
    await assert.rejects(
      () =>
        tool.execute(
          "test-call-3",
          { case_dir: join(workspace, "definitely_missing"), file_path: "system/controlDict" },
          undefined,
          undefined,
          stubContext,
        ),
      "reading a missing case file should reject",
    );
  });

  // 6. shutdown terminates the python child process
  await check("terminates the python process on shutdown", async () => {
    await pi.emit("session_shutdown", {});
    await new Promise((r) => setTimeout(r, 500));
    const stillRunning = await countHarnessProcesses(workspace);
    assert.equal(stillRunning, 0, "the harness process was still running after shutdown");
  });

  rmSync(workspace, { recursive: true, force: true });

  for (const name of passes) console.log(`  ok   ${name}`);
  for (const failure of failures) console.error(`  FAIL ${failure}`);
  console.log(`\n${passes.length} passed, ${failures.length} failed`);
  process.exit(failures.length === 0 ? 0 : 1);
}

async function countHarnessProcesses(workspace) {
  const { execFile } = await import("node:child_process");
  return new Promise((resolvePromise) => {
    execFile("ps", ["-eo", "args"], (error, stdout) => {
      if (error) return resolvePromise(0);
      const matches = stdout
        .split("\n")
        .filter((line) => line.includes("harness.mcp_server") && line.includes(workspace));
      resolvePromise(matches.length);
    });
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
