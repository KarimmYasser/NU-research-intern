# Local LLM Backend Comparison: Ollama vs vLLM vs Alternatives

For SESC-2026-02's Phase I requirement: deploy a responsive local LLM API on an isolated Ubuntu VM (per `ConceptNote-1.pdf`, "via Ollama or vLLM"). This compares the two named options plus the alternatives worth knowing about, with an eye on what this project specifically needs: agents that both call tools (`blockMesh`, file I/O) and emit strict-schema OpenFOAM dictionaries reliably.

## Ollama

**What it is:** A thin, easy-to-run wrapper around `llama.cpp`. `ollama run <model>` and you're serving — no config files, no orchestration.

**Pros:**
- Fastest path to a working local model; minimal setup, ideal for getting Phase I's "responsive local LLM API" milestone done quickly.
- Uses GGUF quantized models — runs on modest hardware, including CPU-only (~10–30 tok/s for a 7B model at Q4_K_M on a modern CPU), so it doesn't hard-require a GPU on the VM.
- Ships an OpenAI-compatible API and structured-output support via a `format` (JSON Schema) parameter, using XGrammar under the hood for constrained decoding.
- Best single-request latency at low concurrency — in one 2026 benchmark, Ollama edged out vLLM on Llama 3.1 8B at single-user load (p99 ~4.3s vs vLLM's 3.8s, close either way).

**Cons:**
- Falls badly behind under concurrency: peak throughput benchmarks show vLLM at ~793 tok/s vs Ollama's ~41 tok/s, and Red Hat's 2026 numbers show vLLM at ~2.3x Ollama's throughput at just 8 concurrent users. Not a problem for a single-agent-at-a-time pipeline, but matters if multiple agents query the model concurrently.
- Less mature structured-output tooling than vLLM/SGLang for complex, nested schemas (OpenFOAM dicts have deep nesting).

## vLLM

**What it is:** A UC Berkeley research project turned production standard for high-throughput LLM serving, built around PagedAttention for efficient GPU memory use.

**Pros:**
- The most mature production-grade server when concurrent load matters; large throughput/latency advantage under multi-request load (see above).
- Strong structured-output support: `guided_json` with a choice of three constrained-decoding backends (XGrammar, Outlines, LLGuidance), each with different speed/flexibility tradeoffs — XGrammar is the fastest for most schemas as of 2026.
- Directly evaluated by name in one of the papers already pulled for this project's literature review (`temp/paper-research-agentic-cfd-llm/pdfs/2605.19537_InferenceBackendsReproducibility.pdf`) for run-to-run reproducibility — worth reading before committing, since reproducible case generation is one of your KPIs.

**Cons:**
- No native GGUF support — expects HuggingFace SafeTensors, GPTQ, AWQ, or FP8. GPU-centric quantization formats (GPTQ/AWQ) require the full model to fit in GPU VRAM with no CPU offload, unlike GGUF. If the isolated VM doesn't have a capable GPU, vLLM is a much harder sell than Ollama.
- Heavier setup and operational surface than Ollama — worth it only if you actually need the throughput.
- Per the "Constraint Tax" paper already in your literature review (`2606.25605`), open-weight models under vLLM/SGLang-style simultaneous tool-calling + JSON-schema constraints can suppress tool calls ("Constraint Priority Inversion") — directly relevant since your agents need both.

## SGLang (not named in the concept note, but worth knowing)

**What it is:** A serving framework purpose-built for structured generation and agentic workflows, not just raw throughput.

**Pros:**
- Fastest time-to-first-token for single requests (80–120ms, 30–40% faster than vLLM in 2026 benchmarks) — relevant for an interactive multi-agent loop where agents call each other repeatedly.
- Native structured output + prefix caching, which is exactly the "agent calls tool, then emits schema-constrained dict, repeatedly" pattern your Meshing/Physics/Syntax-Validation agents will run.

**Cons:**
- Not optimized for maximum throughput or many simultaneous users — the opposite tradeoff from vLLM. Fine for a small internal multi-agent pipeline, not for serving many people at once (not a concern here).
- Smaller ecosystem/community than vLLM or Ollama; more to debug yourself if something breaks.

## llama.cpp

**What it is:** The underlying inference engine Ollama wraps. Can be run directly instead of through Ollama's abstraction.

**Pros:** Lightest weight, works on essentially any hardware (including no GPU), fastest way to swap between GGUF models for testing which local model is actually good enough for your task.
**Cons:** No built-in multi-agent/serving conveniences — you'd be building the API layer yourself, which Ollama already gives you for free. Only worth it if you specifically need to squeeze performance Ollama's abstraction is costing you.

## Ruled out

- **TGI (HuggingFace Text Generation Inference):** archived and in maintenance mode as of March 2026 — not a sensible starting point for a new project.
- **LM Studio:** desktop/GUI-focused; not built for headless server deployment on an isolated VM driving a multi-agent pipeline.

## Recommendation for this project

Given the concept note explicitly allows either Ollama or vLLM, and Phase I's actual goal is just "a responsive local LLM API" by Week 2, not maximum throughput:

1. **Start with Ollama.** It gets you to the Week 2 milestone fastest, works regardless of whether the VM has a GPU, and its structured-output support (XGrammar-backed) is good enough to start testing OpenFOAM dict generation early.
2. **Re-evaluate vLLM (or SGLang) in Phase II–III** once the agent topology is running and you know whether the bottleneck is throughput (multiple agents hitting the model concurrently) or reliability of structured/tool-calling output — at which point SGLang's agent-native structured output may be a better fit than vLLM's raw throughput, if the isolated VM has a decent GPU.
3. Before finalizing either, read the "Constraint Tax" (`2606.25605`) and reproducibility (`2605.19537`) papers already downloaded — they bear directly on whether your agents' tool-calling reliably survives alongside strict JSON-schema output on whichever backend you pick.

## Sources

- [vLLM vs Ollama vs LM Studio: The 2026 Production Self-Host Benchmark](https://codersera.com/blog/vllm-vs-ollama-vs-lm-studio-production-2026/)
- [Ollama vs vLLM: Performance Benchmark 2026 — SitePoint](https://www.sitepoint.com/ollama-vs-vllm-performance-benchmark-2026/)
- [vLLM vs SGLang vs Ollama 2026: Production Comparison](https://stackpulsar.com/blog/vllm-sglang-ollama-comparison/)
- [Ollama vs llama.cpp vs vLLM vs TGI vs SGLang](https://sesamedisk.com/local-inference-engines-2026-comparison/)
- [llama.cpp vs. vLLM: Choosing the right local LLM inference engine — Red Hat Developer](https://developers.redhat.com/articles/2026/06/15/llamacpp-vs-vllm-choosing-right-local-llm-inference-engine)
- [Structured outputs in vLLM: Guiding AI responses — Red Hat Developer](https://developers.redhat.com/articles/2025/06/03/structured-outputs-vllm-guiding-ai-responses)
- [SGLang and the Rise of Engine-Level Structured Output](https://builderai.tools/blog/sglang-and-the-structured-output-renaissance)
- [GGUF vs AWQ vs GPTQ vs MLX: LLM Quant Formats 2026](https://www.digitalapplied.com/blog/gguf-vs-awq-vs-gptq-vs-mlx-llm-quantization-formats-2026)
- [Ollama vs vLLM vs llama.cpp: Local Inference Server Comparison (2026)](https://d-central.tech/ollama-vs-vllm-vs-llama-cpp/)
- `temp/paper-research-agentic-cfd-llm/pdfs/2605.19537_InferenceBackendsReproducibility.pdf` and `2606.25605_ConstraintTax.pdf` (already in this repo's literature review)
