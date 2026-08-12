# Agent Framework Comparison & Decision (SESC-2026-02)

Which open-source agent framework should the OpenFOAM case-generation pipeline (Phase II) be built on? This weighs the two candidates the intern asked about (Nous **Hermes-Agent**, earendil-works **pi**) against the realistic field, judged strictly against *our* constraints — not generic "best agent framework" listicles.

All GitHub stats verified via the GitHub API on **2026-07-22**. Framework capabilities cross-checked against docs and the OpenFOAM-agent literature. Where a claim is qualitative judgement rather than a measured number, it says so.

---

## 1. Recommendation

**Build on [LangGraph](https://github.com/langchain-ai/langgraph).** It is the closest structural match to what our pipeline actually is: a deterministic, validation-gated state machine, not a free-form chat swarm. Our topology — Meshing → Physics → write → `blockMesh`/`checkMesh` → route the error back to the owning agent → bounded retry — maps one-to-one onto LangGraph's model (nodes = agent/tool calls, edges = conditional routing on the validation outcome, an explicit typed state object, and a built-in recursion cap to bound the retry loop). It talks to a **local OpenAI-compatible endpoint (Ollama/vLLM) by changing two lines** (`base_url` + a dummy `api_key`), so it satisfies the hard local-LLM constraint with zero workarounds. Decisively: **the current state-of-the-art system in our exact domain, Foam-Agent (RPI, NeurIPS ML4PS 2025), is built on LangGraph** with almost precisely our agent set and error-correction loop — so we are following proven prior art, not guessing. It is MIT-licensed, actively maintained (pushed daily), and — importantly — its low-level API does not hide the raw model I/O, which we need for the constrained-decoding / error-specificity work the literature says matters most.

**Runner-up: [Pydantic AI](https://github.com/pydantic/pydantic-ai).** If, once the graph is sketched, the control flow turns out to stay a shallow linear Meshing→Physics→Validate with only a one-hop retry, then LangGraph's graph boilerplate won't earn its keep, and Pydantic AI's lighter, strongly-typed tool-calling + structured-output layer is the better fit — and it composes *inside* a LangGraph node anyway. **Switch condition:** choose Pydantic-AI-only if we never need conditional multi-branch routing or checkpointing; keep LangGraph the moment the retry/routing logic branches on `FailureClass`.

**A legitimate third option is "no framework yet":** our `src/agents/*` scaffold is already a thin hand-rolled orchestrator, and for a linear pipeline that is defensible and maximally debuggable. The case for adopting LangGraph over hand-rolling is that it gives us state checkpointing, loop bounding, streaming, and LangSmith tracing for free instead of re-implementing them — value that shows up exactly when the retry loop gets non-trivial, which ours will.

---

## 2. Verdict on Hermes-Agent and pi (the two you asked about)

**Neither is the right tool for this job. Do not build the pipeline on either.** Both are real, popular, MIT-licensed, and actively developed — but they are *agent products*, not orchestration libraries for a bespoke domain pipeline. They solve a different problem than ours.

- **Nous [Hermes-Agent](https://github.com/nousresearch/hermes-agent)** (219k★, Python, MIT, created 2025-07-22) — a *self-improving, persistent, multi-platform personal assistant*: it plugs into Telegram/Discord/Slack/WhatsApp/email, runs cron automations, and grows a curated skill/memory library from experience. It *does* support local OpenAI-compatible models and can spawn subagents, so on paper it ticks boxes — but its entire design center (chat-platform front-ends, a self-mutating skill store, long-lived persona memory) is surface area we would spend the internship fighting and disabling, not using. It is built to be a general assistant that *acts*, not a headless, reproducible, validation-gated file generator. Its **24,692 open issues** also signal a fast-moving product surface, not a stable library to pin a graded deliverable to. Wrong category.

- **earendil-works [pi](https://github.com/earendil-works/pi)** (75k★, **TypeScript**, MIT) — an AI-agent *toolkit + interactive coding-agent CLI* (unified multi-provider LLM API, agent loop, terminal UI). Two disqualifiers: (1) it is **TypeScript/Bun**, while our entire stack is Python — the scaffold (`src/agents/*`), the local-serving layer (Ollama/vLLM), OpenFOAM's Python tooling, and every OpenFOAM-agent paper are Python; adopting pi means a language island for no benefit. (2) It is fundamentally a *coding assistant* (a Claude-Code-style harness), not a library for wiring three specialized domain agents into a deterministic pipeline. Wrong language and wrong category.

Both were almost certainly surfaced because they are trending and high-star. Star count is not fit. The fit question is "does this express a validation-gated multi-agent state machine driving local models in Python?" — and for that the answer for both is no.

---

## 3. Comparison table

Scored against our weighted criteria (1 = hard requirement, descending priority). Stars/issues as of 2026-07-22.

| Framework | ★ | License | Lang | 1. Local-LLM | 2. Our pipeline shape | 3. Tool/struct control | 4. Light/hackable | 5. Maturity | 6. Learning curve | Fit |
|---|--:|---|---|---|---|---|---|---|---|---|
| **LangGraph** | 37.9k | MIT | Py | ✅ 2-line | ✅✅ state-machine, native | ✅ low-level, raw I/O | ◑ verbose but incremental | ✅ active, daily | ◑ moderate | **Best** |
| **Pydantic AI** | 18.7k | MIT | Py | ✅ | ◑ typed agents; graph less mature | ✅✅ typed output, native | ✅ light | ✅ active | ✅ easy | **Runner-up** |
| Hand-rolled (our scaffold) | — | — | Py | ✅ | ◑ manual plumbing | ✅✅ total control | ✅✅ | n/a | ✅ (already ours) | Viable fallback |
| smolagents | 28.5k | Apache-2.0 | Py | ✅ | ◑ code-agent paradigm | ◑ code-exec, not file-gen | ✅✅ minimal | ✅ (HF) | ✅ easy | Possible |
| CrewAI | 56.0k | MIT | Py | ✅ | ◑ role-based, hides flow | ◑ | ◑ heavier | ✅ active | ✅ easy | Weak |
| AutoGen / AG2 | 60k / 4.8k | MIT¹ | Py | ✅ | ◑ conversational, non-determ. | ◑ | ◑ | ⚠ project split | ◑ | Weak |
| OpenAI Agents SDK | 28.1k | MIT | Py | ✅ base_url | ◑ handoffs, lighter graph | ✅ | ✅ | ✅ new, active | ✅ easy | Possible |
| Agno | 41.4k | Apache-2.0 | Py | ✅ | ◑ perf-focused, broad | ◑ | ◑ big surface | ✅ active | ◑ | Weak |
| DSPy | 36.3k | MIT | Py | ✅ | ◑ optimizer, not orchestrator | ✅ signatures | ◑ different mental model | ✅ active | ⚠ steep | Niche² |
| Letta (MemGPT) | 23.9k | Apache-2.0 | Py | ✅ | ✗ memory-centric, not pipeline | ◑ | ◑ server-based | ✅ active | ◑ | Off-target |
| LlamaIndex agents | 51.0k | MIT | Py | ✅ | ◑ RAG-first | ◑ | ◑ large | ✅ active | ◑ | Niche² |
| **Hermes-Agent** | 219k | MIT | Py | ✅ | ✗ personal-assistant product | ◑ | ✗ huge surface | ⚠ 24.7k issues | ◑ | **Off-target** |
| **pi** | 75.5k | MIT | **TS** | ◑ | ✗ coding-agent CLI | ◑ | ✗ | ✅ | ◑ | **Off-target (TS)** |

Legend: ✅✅ excellent · ✅ good · ◑ partial/caveated · ⚠ concern · ✗ poor. ¹AutoGen code is MIT; see note in §4. ²Strong for the RAG sub-component, not the orchestrator.

---

## 4. Per-framework notes (honest, scored against criteria 1–7)

**LangGraph** — Purpose-built for stateful, cyclic, conditional agent workflows: you define a typed `State`, register nodes, and wire conditional edges. That *is* our error-routing retry loop. Local models via `ChatOpenAI(base_url=..., api_key="x")` or the bare `openai` client — first-class (one of the few frameworks that connect to local endpoints with zero workarounds). Built-in `recursion_limit` bounds the loop; checkpointers give state persistence and replay; LangSmith gives tracing (optional, and usable fully offline — no telemetry required for the isolated VM). Cost: it is more verbose than role-based frameworks and drags in part of the LangChain package surface, and its docs sprawl. But you adopt it incrementally — keep our `case.py`/`validation_agent.py` as-is and only replace the orchestrator loop. **Best on 1, 2, 3; acceptable on 4; strong on 5; moderate on 6.**

**Pydantic AI** — From the Pydantic team; model-agnostic, local endpoints supported, and its core strength is exactly criterion 3: strongly-typed tool arguments and Pydantic-validated structured output, with the raw model exposed. Lighter and cleaner than LangGraph. Its weakness is orchestration: `pydantic-graph` exists but is younger and less battle-tested than LangGraph for complex conditional routing. Ideal for the *inside* of each agent (typed `draft_*` returns), and a fine standalone choice if the pipeline stays simple. **Best on 3, 4, 6; slightly behind on 2.**

**Hand-rolled (our current `src/agents/*`)** — A plain-Python orchestrator over the OpenAI client. Zero dependency risk, total control, nothing hidden — genuinely the most debuggable option, and it already exists. It just means re-implementing state, loop-bounding, tracing, and retry plumbing ourselves as the pipeline grows. Reasonable to keep for the first working end-to-end pass, then lift into LangGraph. **Best on 4; you own everything on 2/7.**

**smolagents (HF)** — Minimal, elegant, local-friendly. But its signature paradigm is the *code agent* (the LLM writes and executes Python to act), which is a different and heavier-to-sandbox model than our "emit dictionary files + run a shell command" needs. Good library, slightly wrong paradigm. **Good on 1/4/6; ◑ on 2/3.**

**CrewAI** — Very popular, easy role/task mental model, local models supported. But it abstracts the control flow behind roles and "processes," which is the opposite of what a deterministic, gated pipeline wants — you fight the abstraction to get exact routing and hard loop bounds. Great for collaborative brainstorming crews, weaker for a strict validation gate. **◑ on 2/4.**

**AutoGen / AG2** — Conversational multi-agent (agents talk to each other to converge). Powerful, but non-deterministic "conversation until consensus" is a poor match for a pipeline that must produce byte-exact dictionary files and stop. Sustainability concern: the project **split** — Microsoft's AutoGen (0.4 rewrite) and the community **AG2** fork now diverge, so "AutoGen" is two moving targets; Microsoft's repo also shows a slower push cadence (last push months behind the others). Code is MIT (the CC-BY-4.0 the API reports is the docs license). **◑ on 2; ⚠ on 5.**

**OpenAI Agents SDK** — Lightweight, clean handoffs primitive, and it *does* accept a custom `base_url`, so local serving works. Younger and thinner than LangGraph on stateful loops/checkpointing, but a credible lighter alternative if LangGraph feels heavy. **✅ on 1/4/6; ◑ on 2.**

**Agno, DSPy, Letta, LlamaIndex** — All capable, all local-capable, none aimed at our core problem. Agno is broad and performance-marketed (large surface). **DSPy** is a prompt/pipeline *optimizer* — potentially valuable later to auto-tune our agent prompts against a FoamBench-style eval, but it is not an orchestrator. **Letta** is memory/persona-centric — off-target for a stateless generator. **LlamaIndex** is RAG-first — its real value to us is the *retrieval sub-component* (indexing OpenFOAM tutorials, as MetaOpenFOAM/Foam-Agent do), not the orchestration.

---

## 5. Prior art — what the OpenFOAM-agent systems actually built on (highest-signal input)

This is the single most useful data point, because these teams solved a near-identical problem:

| System | Orchestration stack | Notes |
|---|---|---|
| **Foam-Agent** (RPI, NeurIPS ML4PS 2025) — current SOTA | **LangGraph** stateful graph | Architect / Input Writer / Runner / Reviewer agents; conditional-edge error-correction loop (up to 25 iterations); FAISS-RAG over OpenFOAM tutorials. **88.2% success on 110 cases.** MIT. OpenFOAM Foundation v10. |
| **MetaOpenFOAM** (v1/2.0) | **MetaGPT + LangChain** | Assembly-line role-based agents; LangChain + FAISS for RAG retrieval over tutorials/cases. |
| **OpenFOAMGPT** | RAG-augmented LLM agent (LangChain-style) | RAG over OpenFOAM docs; focus on cost-effective automation. |

**Implication:** the LangGraph/LangChain family is the *de facto* standard in our exact domain, and **Foam-Agent's architecture is essentially our design already** — its Architect≈our orchestration, Input Writer≈Meshing+Physics, Runner+Reviewer≈our Validation agent + error-routing loop. Concretely: we should **read Foam-Agent's LangGraph orchestrator as a reference implementation** (MIT, so we can study it directly), and note two things it does that our scaffold hasn't scoped yet — **RAG retrieval over tutorial cases** (grounds generation, a likely accuracy lever) and a **much higher iteration cap** (25 vs our placeholder `MAX_RETRIES = 3`) — both worth raising with the supervisor.

One honesty caveat: Foam-Agent recommends a *frontier* model (Claude Opus) for best generation and only uses local models for embeddings — it hits 100% of FoamBench with Opus. Our project forbids external APIs, so we are deliberately operating in the harder regime (generation on open-weight local models), where quality will be lower than these papers' headline numbers. That does not change the *framework* choice; it raises the importance of criterion 3 (tight control over prompting/constrained decoding) — which favors LangGraph's low-level access over higher abstraction frameworks.

---

## 6. Benchmarks & signals (with sourcing and limits)

Be clear-eyed: **there is no authoritative "agent framework" benchmark.** Framework choice is an engineering-fit decision, not a leaderboard. The measurable signals we *do* have:

- **Domain success rates** (measure the *system*, not the framework, and mostly with frontier models): Foam-Agent **88.2%** across 110 cases (LangGraph); Foam-Agent 2.0 reports 100% on FoamBench with Claude Opus. Useful as a ceiling reference and as evidence LangGraph scales to this task — not as a local-model expectation. Source: [Foam-Agent 2.0, arXiv 2509.18178](https://arxiv.org/abs/2509.18178).
- **Tool-calling reliability on open-weight models** — the thing that actually gates *our* success — from the **Berkeley Function Calling Leaderboard V4** ([leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html), [BFCL paper PMLR v267](https://proceedings.mlr.press/v267/patil25a.html)): the top open-weight models are the **Qwen family** (top open-weight ≈0.73; a 27–32B Qwen lands ≈0.68). Takeaway for us: (a) prefer a **Qwen-class model** for the agents' tool-calling, and (b) even the best open-weight tops out well below frontier — so the framework must let us apply **constrained decoding** (XGrammar/`guided_json` via vLLM/SGLang) and tight schemas, reinforcing our lit-review findings (the "Constraint Tax" and FLARE's "error specificity > retry mechanism"). LangGraph and Pydantic AI both leave that control in our hands; higher-abstraction frameworks tend to obscure it.
- **GitHub health** (maintenance proxy, verified 2026-07-22): LangGraph 37.9k★ / 637 open issues / pushed daily / MIT — healthy. Pydantic AI 18.7k★ / MIT / very active. Contrast the "off-target but trending" repos: Hermes-Agent's 24.7k open issues is a product-churn signal, not a stability signal.

Bottom line: no benchmark overturns the fit-based reasoning; the benchmarks we have (BFCL, Foam-Agent) *reinforce* it — pick LangGraph, run a Qwen-class model, and invest in constrained decoding + specific error feedback.

---

## 7. How LangGraph maps onto our scaffold (`src/agents/*`)

The migration is incremental — **most of the scaffold survives unchanged**; only the orchestrator's hand-rolled `for`-loop becomes a graph.

**Keep essentially as-is:**
- `case.py` — `CaseFiles` + disk writer are pure, framework-agnostic. Becomes the payload carried in the graph's state. **100% kept.**
- `validation_agent.py` — the `blockMesh`/`checkMesh` subprocess runner and the `FailureClass` taxonomy are exactly what a LangGraph *node* wraps, and `FailureClass` is exactly what a *conditional edge* branches on. Kept; wrapped, not rewritten. (Its `parse_errors` remains the highest-value TODO — LangGraph doesn't change that; FLARE still rules.)
- `meshing_agent.py` / `physics_agent.py` domain logic + v13-convention system prompts (`physicalProperties`/`momentumTransport`) — kept; each becomes a node, and its `draft_*` return can be tightened with a Pydantic output model.
- `llm_client.py` — the `LLMConfig`/`base_url` local-endpoint config is reused directly (LangGraph's `ChatOpenAI` takes the same `base_url`), or we keep the raw client inside nodes.

**Change — `orchestrator.py`:** replace the `for attempt in range(MAX_RETRIES)` loop with a `StateGraph`:

```
State = { prompt, case: CaseFiles, patch_names, last_errors, attempt }

nodes:   mesh_node ─▶ physics_node ─▶ write_and_validate_node
conditional edge from write_and_validate_node, keyed on FailureClass:
    ok                       ─▶ END
    MESH_GENERATION / MESH_QUALITY ─▶ mesh_node      (redraft geometry)
    DICTIONARY_SYNTAX (0/ or constant/) ─▶ physics_node  (redraft fields)
recursion_limit = N   ← replaces MAX_RETRIES, bounds the loop
```

This makes the error-routing rule the orchestrator's TODO describes (route `MESH_*` to Meshing, `DICTIONARY_SYNTAX` to Physics) a **declarative edge** instead of imperative `if/else`, and the retry bound a graph setting instead of a counter. The open decisions in `src/README.md` stay open and get cleaner homes: *who owns `controlDict`/`fvSchemes`/`fvSolution`* becomes "which node writes them" (likely a third `system_node` or folded into `physics_node`); the *Meshing→Physics handoff order* becomes the fixed edge order; *checkMesh exit-code detection* stays a `validation_agent` concern. Nothing in the v13-vs-old-convention discipline changes.

**Net:** adopting LangGraph is a targeted rewrite of one file (`orchestrator.py`) plus thin node wrappers — not a scaffold teardown. That incrementality is itself a reason to prefer it over a heavier role-based framework that would want to own the whole structure.

---

## 8. Open questions for the supervisor

1. **Framework choice is partly a VM/GPU question.** LangGraph is the recommendation regardless, but constrained decoding (needed for reliable tool-calls on open-weight models, per §6) is a **vLLM/SGLang** feature — Ollama's support is weaker. If the VM has **no GPU**, we run smaller models on Ollama with looser tool-calling and lean harder on the validation-retry loop; if it has a **GPU**, vLLM + guided decoding is the stronger setup. This ties directly to the still-open VM/GPU question in `first-meeting-prep.md`.
2. **Is studying/borrowing from Foam-Agent (MIT) acceptable** as a reference architecture, or does he want a clean-room build for the learning outcome? It is the closest prior art and would de-risk Phase II substantially.
3. **RAG over tutorial cases** (as Foam-Agent/MetaOpenFOAM do) is out of our current scaffold's scope but is a proven accuracy lever — in scope for the 8 weeks, or a stretch goal?
4. **Retry budget:** Foam-Agent allows up to 25 correction iterations vs our placeholder 3. On local models each iteration is cheap (no API cost) but slow — worth agreeing on a target.

---

*Sources:* [LangGraph](https://github.com/langchain-ai/langgraph) · [Pydantic AI](https://github.com/pydantic/pydantic-ai) · [Hermes-Agent](https://github.com/nousresearch/hermes-agent) · [pi](https://github.com/earendil-works/pi) · [Foam-Agent repo](https://github.com/csml-rpi/Foam-Agent) · [Foam-Agent 2.0 paper (arXiv 2509.18178)](https://arxiv.org/abs/2509.18178) · [MetaOpenFOAM (arXiv 2407.21320)](https://arxiv.org/abs/2407.21320) · [BFCL V4 leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html) · [BFCL paper (PMLR v267)](https://proceedings.mlr.press/v267/patil25a.html) · [ChatOpenAI local-endpoint docs](https://docs.langchain.com/oss/python/integrations/chat/openai). GitHub stats via GitHub API, 2026-07-22.
