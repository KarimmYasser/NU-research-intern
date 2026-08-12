# Research Direction: Reinforcement Learning for Agent Skills

**Status: exploratory research thread (intern-initiated), forward-looking beyond the core Phase II–IV plan.** This doc frames *why* our pipeline is an unusually good fit for reinforcement-learning-style self-improvement, and how we'd approach it. It synthesizes two research passes — an internal engineering/feasibility pass ([`../temp/rl-agentic-research/findings.md`](../temp/rl-agentic-research/findings.md)) and an external primary-source academic pass ([`../temp/rl-agentic-research/external-advanced-findings.md`](../temp/rl-agentic-research/external-advanced-findings.md)) — both now folded in. Load-bearing claims were repo/arXiv-verified (stamped ✅); confidence is flagged throughout.

## The thesis: we already have the hard part

Reinforcement learning with verifiable rewards (RLVR) — the family of techniques behind recent reasoning models — depends on one thing that is usually the bottleneck: **a reward signal you can trust and automate.** Most agentic-RL efforts have to hand-craft or learn a reward model. We don't. Our [validation ladder](phase-ii-architecture.md) is already an objective, fully-automatable scorer of an agent's output:

| Ladder rung | Signal it produces | As a reward |
|---|---|---|
| Rung 1 — static/schema | valid dict syntax? v13 names? patches match? | dense, cheap, per-file partial credit |
| Rung 2 — `blockMesh`/`checkMesh` | does the mesh generate? quality metrics | graded (generated / quality score) |
| Rung 3 — `foamRun` first step | does the case actually run? | the real terminal reward |

So the pipeline emits, for free, exactly the `(trajectory → reward)` pairs any RL method consumes. **This is the strongest argument for pursuing RL here: the expensive prerequisite is a byproduct of the core design, not extra work.**

## What "a skill" could mean here

The intern's framing is "making skills using RL." That word spans several concrete senses; the research points to the first as our starting sense:

- A **reusable artifact the agent writes and saves** on success (e.g. a verified `blockMeshDict` template for "2D channel", a correction recipe for a recurring error) — a *skill library*, [Voyager](https://arxiv.org/abs/2305.16291)-style, requiring **no weight training**. **← recommended starting sense.**
- A **learned policy behavior** — the model's weights fine-tuned so it produces valid v13 cases more often (RFT / GRPO). Requires training + hardware.
- A **distilled routine / prompt** discovered by keeping only high-reward trajectories. Middle ground.

## Spectrum of approaches (cheapest → heaviest)

Ordered by cost and hardware demand. An 8-week project realistically starts at the top; the bottom is a research-grade stretch.

1. **No weight update — self-improvement loops.** Reflection over failures, a growing skill/template library gated by the validators, expert-iteration bookkeeping. Runs on the same local inference we already need; **no GPU training required.** Most feasible; our starting rung.
2. **RFT / expert iteration / STaR.** Log trajectories, keep validator-passing ones, supervised-fine-tune the local model on the winners. Needs modest training hardware (LoRA). The literature backs a **SFT cold-start → RL** ordering (SFT on easy-tier solutions beats mixing).
3. **True policy-gradient RLVR.** GRPO/DAPO (not PPO — see §"Algorithm & hardware") on the local model with the validator as reward. Most powerful, most compute-hungry and least stable; feasibility hinges on the open **VM/GPU** question.

## Automatic curriculum: difficulty progression as the loop

**Passing a task is a weak signal of competence — capability is how far up a difficulty ladder the agent reliably reaches.** This is *automatic curriculum learning* (the engine behind Voyager's skill acquisition; a well-studied idea in curriculum RL / unsupervised environment design). Instead of "generate case → pass/fail → done," the loop escalates: on success, hand the agent a harder task; continue until it **saturates** — difficulty escalation stops yielding improvement and the recurring failure points at the frontier stop shrinking. That ceiling is a far more honest measure of the pipeline than a single pass.

The curriculum plays two roles at once:
- **Evaluation** — capability = the highest difficulty rung the agent clears reliably (not just once). This is our real benchmark.
- **Training-signal generator** — keep the agent working at the *edge* of its ability ("zone of proximal development"), where attempts are maximally informative, rather than burning episodes on already-solved tasks. Over episodes, the frontier advances and per-rung failure rate drops — the "failure points vanish over time" the intern described.

**We get the difficulty axis for free.** CFD supplies a natural, externally-validated ordering — roughly FoamBench's 11 physics categories:

```
2D laminar (cavity) → steady turbulent RAS (pitzDaily) → transient →
conjugate heat transfer → multiphase → combustion / reacting →
shock / compressible → complex geometry (snappyHexMesh)
```

Each step up adds files, physics models, and BC complexity — and each is still scored by the same validation ladder. So the curriculum is concrete and hand-authorable from day one (auto-*proposing* novel tasks beyond a fixed ladder is the harder, later research question ⏳).

Honest caveat: at the frontier the agent fails often, so reward is sparse there — the loop must balance edge-of-ability tasks (learning signal) against enough solvable ones (stable gradient / non-zero reward). Reward shaping from the ladder's partial credit (rung 1/2 before rung 3) helps here.

### The concrete algorithm: Self-Evolving Curriculum (SEC)

The external research pass turns the "escalate until saturation" intuition into a specific, published method: **SEC** ([arXiv 2505.14970 ✅](https://arxiv.org/abs/2505.14970), MIT code [`ServiceNow/sec`](https://github.com/ServiceNow/sec)). It rests on a clean result: under GRPO with a binary reward, the expected learning signal is

$$\mathbb{E}[|\hat{A}_i|] = 2\sqrt{p(1-p)},\quad\text{maximized at } p=0.5$$

i.e. **the richest gradient comes from tasks the model passes ~half the time** — the zone of proximal development, made exact. SEC then frames curriculum choice as a **non-stationary multi-armed bandit**, one arm per difficulty tier (our 11 CFD categories): each step, reward a tier by the mean `|advantage|` of its rollouts, EMA-update a per-tier Q-value, and Boltzmann-sample the next tier `p(c) ∝ exp(Q_c/τ)`. It's algorithm-agnostic (works with GRPO/PPO/RLOO) and near-zero overhead. Reported: +13–33% over a random curriculum; a *reversed* curriculum hurts — ordering genuinely matters.

```python
# per-tier bandit over the CFD difficulty ladder; Boltzmann sampling on learning-gain
Q = {tier: 0.0 for tier in CFD_TIERS}        # 11 tiers
def sample_tier():                            # pick where |advantage| is largest
    v = np.array([Q[t] for t in CFD_TIERS]); p = np.exp(v/tau)/np.exp(v/tau).sum()
    return np.random.choice(CFD_TIERS, p=p)
def update(tier, advantages):                 # EMA of mean |advantage|
    Q[tier] = alpha*np.mean(np.abs(advantages)) + (1-alpha)*Q[tier]
```

**Saturation falls out for free:** when a tier's Q collapses toward zero (pass rate → 0% *or* → 100%), the sampler routes budget away automatically — no hand-set threshold. On all-pass, archive that tier's cases to the skill library and keep a ~5% maintenance floor (anti-forgetting); on all-fail, mark "frontier not yet reachable" and wait for easier tiers to lift. **Sparse-reward protection:** pair with DAPO's *dynamic sampling* — discard any batch where all rollouts get identical reward — so an all-fail hard-tier batch never produces a zero-gradient, destabilizing step. (Concurrent work DUMP reaches the same insight; AdaRFT is a simpler hand-scheduled alternative.)

## Honest feasibility framing

- **The reward is real; the training may not be affordable.** Rung 1 (self-improvement, no training) is achievable regardless of hardware and is genuinely useful. Rungs 2–3 depend entirely on the VM/GPU we don't yet have — GRPO/LoRA fine-tuning of even a 7B model needs a capable GPU. This couples directly to the still-open hardware question in [`../supervision/first-meeting-prep.md`](../supervision/first-meeting-prep.md).
- **No published OpenFOAM system uses *true* RL — confirmed by two independent searches.** The SOTA (Foam-Agent etc.) relies on frontier models + RAG + retry. The closest prior art is **supervised fine-tuning / expert-iteration**: FoamGPT (SFT on Qwen2.5-7B; a real, MIT-licensed [training dataset](https://huggingface.co/datasets/LeoYML/FoamGPT) exists ✅, though its headline exec-rate improvement is ⚠️ unverified at the paper level and the dataset is **v10-convention** — same v13 remap as our RAG corpus) and AutoCFD ⚠️. So SFT of a local 7B model on OpenFOAM cases is a *documented, viable* rung; verifiable-reward RL (RLVR/GRPO) on OpenFOAM is genuinely **novel** — a strong report contribution, but with no published blueprint (higher risk). The nearest *structural* analogues to lean on are outside CFD: BashCoder-R1 (GRPO for shell scripts, sandbox-execution reward ⚠️) and RLVR for theorem provers (binary pass/fail identical in shape to `foamRun` succeeding ⚠️).
- **Reward hacking is a CFD-specific trap.** A case that "runs" (`foamRun` exits 0) is not necessarily physically correct. Any reward must resist the agent learning to produce trivially-runnable-but-wrong cases — see §"Reward design & hacking" for the concrete mitigations and the recommendation to *fuzz the verifier before training*.

## Reward design & hacking

Shape the ladder into a graded reward rather than a single pass/fail — partial credit per rung gives a gradient even when the case doesn't fully run (starting weights, tune empirically):

`R = 0.1·schema + 0.2·blockMesh + 0.3·checkMesh + 0.4·foamRun`, times a **physics-plausibility multiplier** (×1.0 / ×0.5).

The physics multiplier is the important part: the primary hack vector in CFD is a case that *runs but is physically wrong*. Concrete guards (each cheap, each closes a known exploit):

| Exploit | Guard |
|---|---|
| Runs but diverged | gate the `foamRun` reward on converged residuals (`foamLog`, final residual < 1e-4) |
| Description ignored (turbulent prompt → laminar model) | bind reward to description-matching: mismatch ⇒ reward 0 |
| Trivial 1×1×1 cube mesh regardless of geometry | check cell count vs. described complexity |
| Residual gaming via over-tight relaxation | require ≥N iterations before the residual check |
| Memorize & replay a few passing cases | hash verified cases; penalize duplicate outputs |

**Strong recommendation from the literature: *fuzz the verifier before any training*** ("Before the Model Learns the Bug" ⚠️) — deliberately search for inputs that pass the reward without being correct, and close them, *before* an RL policy finds and exploits them for you. This applies directly to our validation ladder.

## Algorithm & hardware (rung 2–3 reality)

- **Use GRPO / DAPO, not PPO.** PPO keeps a critic (~2× resident memory) → single-GPU 7B PPO is effectively infeasible. GRPO ([2402.03300 ✅](https://arxiv.org/abs/2402.03300)) is critic-free; **DAPO** ([2503.14476 ✅](https://arxiv.org/abs/2503.14476)) adds the dynamic-sampling fix our curriculum needs (drops all-identical-reward batches).
- **PEFT: DoRA (or AdaLoRA/MiSS) over plain LoRA** for RLVR; *avoid* SVD-init methods (PiSSA/MiLoRA — they collapse under RLVR ⚠️). Rank 16–32.
- **Hardware (⚠️ report-flagged estimate, verify empirically):** 7B GRPO+LoRA needs **≥40 GB VRAM** for stability (one A100-40GB with grad-checkpointing + ZeRO-2 offload, or 2×24 GB). A single 24 GB card for *7B GRPO* is marginal→infeasible; 24 GB is fine for the cheaper *SFT* rung. 14B ⇒ ≥80 GB.
- **The real bottleneck is rollout throughput, not VRAM** — OpenFOAM episodes are slow, so generating enough scored trajectories dominates wall-clock. This alone argues for stopping at the SFT rung unless a good GPU is confirmed.

## Tooling and the on-ramp we'd actually take

**Recommended path:** climb the rungs; stop where the hardware (and the 8 weeks) run out. Rungs 0–2 need no GPU and already deliver a demoable "agent that learns skills."

| Rung | Needs | What it is | 8-wk? |
|---|---|---|---|
| **0 — instrument** | none | ladder → numeric reward + trajectory logger. Prerequisite; *is* the dataset. | ✅ |
| **1 — skill library** | none / CPU | Voyager-style verified skill library on the existing FAISS infra; retrieve/compose. | ✅ |
| **2 — curriculum routing + failure memory** | none / CPU | per-tier pass-rate tracking, prompt-level SEC routing, Reflexion-style negative examples. **Recommended target.** | ✅ |
| **3 — expert-iteration SFT** | 1×24 GB ⚠️ | LoRA-SFT on validator-passing winners (FoamGPT recipe, at v13). Ideal reach. | ✅ feasible |
| **4 — GRPO+DoRA+DAPO (no curriculum)** | 1×A100-40 GB ⚠️ | true RLVR with the ladder as reward. | ⚠️ tight |
| **5 — GRPO + SEC curriculum (veRL)** | 1×A100-40 GB ⚠️ | the full vision; the genuine novelty. | ⚠️ research push |
| **6 — GRPO on 14B** | ≥2×A100-40 GB | — | 🚫 future work |

**Realistic stop: a solid Rung 2, ideally reaching Rung 3 (SFT).** Rungs 4–5 are the novelty but need a confirmed GPU and RL-debugging time; frame them as "prototyped + future work."

**Tooling — two complementary layers:**
- *Training stack (paper-grounded):* **TRL** (Apache-2.0; `reward_funcs` takes our verifier directly; DoRA via PEFT) for SFT cold-start + simple GRPO → **veRL + SEC** (SEC's MIT code is built on veRL) for curriculum GRPO → **RAGEN / SkyRL-Agent** if the LangGraph tool calls become part of the training trajectory (multi-turn). All Apache/MIT, no external APIs.
- *Agent↔trainer integration (repo-verified):* **OpenPipe ART** (Apache-2.0, 10.5k★ ✅) and **Microsoft Agent Lightning** (MIT, 17.4k★ ✅) *decouple the agent from the trainer* — the LangGraph pipeline keeps running while the trainer observes trajectories + rewards and updates weights out-of-band. (The external academic pass found no *papers* for these and declined to assess them; they're real, active repos, verified directly.) This decoupling is why adopting RL later needs **no rewrite** — a strong reason to keep the agent/trainer boundary clean now.

## What already makes the repo RL-ready

No speculative RL code is committed yet (deliberately — approach TBD from research). But the current design is already pointed the right way:

- The **validation ladder** is the reward function.
- The **per-node self-healing loop** already generates and scores trajectories; logging those `(input, output, validator result)` tuples is the data collection any RL rung needs — a cheap, high-value first step that pays off even if we never train weights.
- **`FailureClass`** in `validation_agent.py` is the beginning of a structured reward/critique signal.

**The five repo changes that serve this goal** (ordered by leverage; none commits us to training):

1. **Reward as a first-class module (`reward.py`)** — the §2.1 validation ladder returns a *numeric shaped score* (`+w1·fraction-files-valid → +w2·blockMesh → +w3·checkMesh-quality → +w4·foamRun → +w5·converges`, minus per-heal penalties), consumed by *both* the self-healing loop and any future trainer. Highest-leverage change; makes reward explicit instead of implicit in pass/fail.
2. **Trajectory / episode logger** — persist every generation attempt (prompt, retrieved context, files, per-rung reward, heal history) as JSONL. *Is* the RFT/GRPO dataset; near-free to add now and useful immediately for debugging/evals.
3. **Verified skill library** — reuse the RAG FAISS infra to store the agent's *own* ladder-verified case templates (Voyager pattern), retrievable alongside human tutorials.
4. **Keep the agent/trainer boundary clean** — so a trainer (ART/Agent Lightning) can attach later out-of-band; make `llm_client.py` swap to a fine-tuned checkpoint via env var.
5. **v13-remap discipline** — applies to the FoamGPT dataset too, if we reuse it (it's v10).

Reward-hacking guards live in §"Reward design & hacking" — of the five changes, `reward.py` should embed the physics-plausibility multiplier from the start.

## Confidence & what's genuinely open

Both research passes are folded in and their load-bearing claims verified (SEC, GRPO, DAPO, the ART/Agent Lightning repos, the FoamGPT dataset — all ✅). What remains genuinely open is **not more research** but **project decisions and empiricism**:

- **Hardware (the gate).** Everything above Rung 3 depends on the still-open VM/GPU question ([`../supervision/first-meeting-prep.md`](../supervision/first-meeting-prep.md)). No/weak GPU ⇒ ship Rungs 0–2 (a complete, demoable deliverable); one A100-class GPU ⇒ Rungs 3–5 open. This is now the *second* thing riding on that answer (structured decoding was the first).
- **Scope call for the supervisor.** Is RL a *core deliverable* or an *exploratory extension*? That decides the time budget past Rung 2.
- **Empirical unknowns no paper can answer:** our local model's actual pass-rate per CFD tier, the real peak VRAM (measure with a TRL GRPO smoke test), and the reward weights `w1..w5` (tune, don't trust the defaults). The ⚠️-flagged hardware/benchmark numbers are estimates — treat the first prototype as the measurement, not the paper.
