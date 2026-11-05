# DeepSeek Harness (dsh) Source-Study Outline

> Progressive chapter outline: each chapter adds one new mechanism on top of the previous one; Chapter 17 integrates all mechanisms into an end-to-end flow, and Chapter 18 closes from a cross-cutting angle.
> Terms are defined at their first appearance within a chapter; a "Terminology Naming Table" is appended at the end for cross-chapter consistency reference.
> Body text in English, code/symbols in English, diagrams in Mermaid.
> Teaching code: `chNN/code/` accumulates incrementally per chapter (one self-contained directory per chapter).
> Version anchor: finalized against upstream HEAD `8bd0733` (dsh-v0.2.2-alpha.3, 2026-11-03).

## 0. Overview

- **Worldview**: everything is a plugin; agent = model + harness; registration is effect; the seam triple-role.
- **Chapter count**: 18 chapters (ch01–ch18). ch01–ch16 thicken layer by layer, ch17 integrates the main line, ch18 closes from a cross-cutting angle.
- **Reading path**: read the vendored Cordis primitives first → the minimal agent-loop closed loop → thicken mechanism by mechanism → Chapter 17 brings up one startup path → Chapter 18 observes that running system (how events leave the process).
- **Chapter positioning**: Chapter 17 remains the main-line integration chapter (one startup path + one execution loop + one code-execution host); Chapter 18 is not a second integration chapter — it is the cross-cutting close, answering only "after the system runs, how does each kind of event leave the process".
- **Reader's starting point**: no need to read the whole repository first; build the mental model chapter by chapter along the outline.

## 1. The Cordis Container Kernel (Everything Is a Plugin)

- **Topic**: how the plugin container is assembled (the minimal core of context/services/events).
- **New mechanisms**: `Context` service container + `Service` + plugin registration + `inject` dependency checking + `Fiber`/`effect` reversible registration + `on`/`emit`/`serial`/`waterfall` event dispatch.
- **Corresponding source modules**: `vendor/cordis` (vendored from the Cordis kernel; this repo's package name `@deepseek-ai/cordis`; files context/service/registry/events/fiber/reflect/utils).
- **Questions this chapter answers**: what are context, service, reversible registration, and event dispatch; how one `Context` unifies them.
- **New teaching code in this chapter** (`ch01/code/`, self-contained, standard library only, runnable via `python ch01/code/hello.py`):
  - `cordis.py`: teaching edition of the Cordis kernel.
  - `hello.py`: the minimal runnable example in §2.
  - `bad_example.py`: the counter-example in §1.

## 2. The Minimal agent-loop Closed Loop (One Message In, One Reply Out)

- **Topic**: how a user message becomes a reply — the minimal runnable closed loop.
- **New mechanisms**: `Session` (in-memory append-only) + `SystemPromptService` (+`render_prompt`) + `AgentLoop` (kick/turn/step).
- **Corresponding source modules**: `packages/core/agent-loop` (`agent.ts` / `inbox.ts`) + `packages/core/session` (now split into 10 files: surface/repair/fork/seq-ranges/preparation/…) + `packages/core/system-prompt` + llm stub.
- **Questions this chapter answers**: where conversation state lives, where the system prompt comes from, who drives the loop; how an agent gets assembled by the container.
- **New teaching code in this chapter** (`ch02/code/`, incrementally reusing `cordis.py` from ch01, runnable via `python ch02/code/main.py`):
  - `agent_loop.py`: teaching edition of the agent-loop.
  - `main.py`: runnable assembly entry.

## 3. Session Event Stream, Handle Persistence, and Format Migration (Silent on Write, Traceable on Cold Read)

- **Topic**: how conversations are recorded, persisted, replayed, and read across versions.
- **New mechanisms**: `SessionEvent` append log + the `SessionPersistence`/`SessionHandle` ownership contract (seven error classes) + the backend-owned live write path + the format chain v0→v4 (adjacent migration edges) + cold-read projection and cache + capture-side telemetry (authorization and egress live in Chapter 18).
- **Corresponding source modules**:
  - `packages/session/session-persistence` (`index.ts` service contract, `handle.ts`, `errors.ts`), `session-persistence-jsonl` (the only session backend: single-writer routing, batching window, durability barrier, torn-tail recovery).
  - The six format-chain packages: `packages/session/session-format`, `session-format-catalog`, `session-format-v0-to-v1`, `session-format-v1-to-v2`, `session-format-v2-to-v3`, `session-format-v3-to-v4`.
  - `packages/session/session-projection`, `session-projection-cache`, `session-title`, `session-telemetry` (capture and redaction).
  - `packages/storage/{storage,storage-domain,storage-json,storage-sqlite}`: only non-session application data (workspace records, session sidecars); drawn as a boundary against the persistence seam.
  - `packages/core/session` (`repair.ts`, `surface.ts`): interrupted-turn repair and cold-read folding.
- **Questions this chapter answers**: how events reach disk without disturbing the running loop; why a log has exactly one writer and what enforces it; how cold reads recover interrupted turns and torn tails; how old-version logs are read, and why unreadable ones must be refused rather than guessed.
- **New teaching code in this chapter** (`ch03/code/`, incrementally reusing `cordis.py` from ch01 and `agent_loop.py` from ch02, runnable via `python ch03/code/main.py`):
  - `session_persistence.py`: the handle edition (`SessionHandle` contract + a single JSONL backend + the live write path + a v3→v4 migration demo + capture-side telemetry).
  - `main.py`: runnable assembly entry (8-stage closed loop, including the format-migration stage).
  - `bad_example.py`: the counter-example in §1.

## 4. tools Registry and Executor Pipeline (Define First, Present Next, Guard at the Gate)

- **Topic**: how the model acquires tools: define them first, then decide presentation, then guard every call.
- **New mechanisms**: `ctx.tools` scoped registration + restriction (tool mask) + ToolView and schema projection + the pre-execute/execute/post-execute three-stage waterfall + an executor seam parallel to the registry (native executor / PTC executor).
- **Corresponding source modules**:
  - `packages/core/tools` (`index.ts` now only registration and layers; `contracts.ts`, `presentation.ts`, `pipeline.ts`, `batch.ts`, `executor.ts`, `schema.ts`).
  - `packages/core/tool-executor-native` (native executor), `packages/core/tool-executor-ptc` (PTC executor).
  - `packages/core/agent-tool-presentation` (Deprecated: the old selector row, migrated to an executor row).
  - `packages/guard/*`, `packages/deliverables/tool-present` (the `present` instance inside the executor).
- **Questions this chapter answers**: how tools are registered, isolated, hidden, and overridden; where the model learns which tools exist and who decides their schemas; which gates one call passes; why PTC and native share one registry and why swapping executors leaves the registry untouched.
- **New teaching code in this chapter** (`ch04/code/`, incrementally reusing ch01/ch02):
  - `tools.py`: registry + ToolView + the three-stage waterfall + two executor stubs (native / PTC, including `run_code`).
  - `main.py`: a segment demonstrating "swap the executor, the registry stays put".
  - `bad_example.py`: the counter-example in §1.

## 5. The Triple Role of the capability seam

- **Topic**: how one capability is decomposed into "definition, implementation, consumption".
- **New mechanisms**: the three roles of Service Definition / Service Provider / Consumer + the full seam contract (using shell as the example, `resolve`/`execute` abstract methods) + a comparison of three variants (empty-definition style, descriptive fields, whole-provider replacement).
- **Corresponding source modules**: `packages/shell/shell`, `packages/shell/bash-local`, `packages/shell/tool-bash`; `docs/capability-seams.md`.
- **Questions this chapter answers**: how a capability splits into three decoupled roles; how a method-call-style seam differs from the event-waterfall pipeline; why swapping the implementation leaves the definition and consumer untouched.
- **New teaching code in this chapter** (`ch05/code/`): `shell.py`, `bash_local.py`, `tool_bash.py`, `sandbox_executor.py`, `main.py`, `bad_example.py`.

## 6. The subprocess / shell / fs Execution World

- **Topic**: the execution surface that lets the agent actually do things on the machine.
- **New mechanisms**: `ctx.subprocess` process tree + `ctx.fs` (version guard, atomic write, intent waterfall) + `ctx.sandbox` isolation + `workspace/changes` double snapshots + the SSH provider family (wholesale replacement to a remote machine).
- **Corresponding source modules**:
  - `packages/subprocess/{subprocess,subprocess-local}`, `packages/shell/{shell,bash-sandbox}`, `packages/fs/{fs,fs-local,fs-sandbox,fs-patch}`, `packages/sandbox/{sandbox,sandbox-local}`.
  - `native/system` (`landlock-run` remains a subpath of it).
  - `packages/ssh/{ssh,ssh-helper-runtime,subprocess-ssh,fs-ssh,sandbox-ssh}` (five packages).
  - `packages/deliverables/workspace-changes` (consumer of the intent waterfall, double snapshots, `workspace/changes` events).
- **Questions this chapter answers**: why the standard-library `subprocess` cannot be handed to an agent directly; what a "process tree" means and why terminate must kill the whole tree; why the filesystem is a seam; how sandbox wraps the runner without touching the tool layer; which files a turn changed and why the event carries only a turn number; how many lines a consumer changes when the execution world moves to another machine.
- **New teaching code in this chapter** (`ch06/code/`): `subprocess_service.py`, `fs_service.py`, `sandbox_service.py`, `bash_sandbox.py`, `workspace_changes.py`, `remote_world.py`, `mock_runner.py`, `main.py`, `bad_example.py`.

## 7. LLM Adaptation and Streaming

- **Topic**: how the model layer is swapped and how streaming output works.
- **New mechanisms**: `ctx.llm` adapter seam + a unified chunk streaming protocol + the credentials seam + the SettingsForms editing face + the request-extension registry (the `register→prepare→accept` transaction).
- **Corresponding source modules**: `packages/llm/{llm,llm-deepseek,llm-pi-ai,llm-retry,token-meter,deepseek-llm-api-extensions,plugin-package-inventory-deepseek}`; `packages/credentials/credentials`, `packages/settings/settings` (`SettingsForms`, not a key-value store).
- **Questions this chapter answers**: how the stub is swapped for a real model without touching callers; how per-provider streaming formats are hidden behind one chunk protocol; where secrets live; how a plugin contributes a request field without editing the adapter.
- **New teaching code in this chapter** (`ch07/code/`): `llm_runtime.py`, `deepseek_adapter.py`, `pi_adapter.py`, `credentials.py`, `token_meter.py`, `main.py`, `bad_example.py`.

## 8. system-prompt Assembly and context

- **Topic**: where the "prompt" sent to the model comes from.
- **New mechanisms**: prompt section registration/merging + workspace instructions + time context + `session-reference` + `deliverable-file-references` (a fixed-order pure prompt segment).
- **Corresponding source modules**: `packages/core/system-prompt`, `packages/context/{agent-instructions,time-context,session-reference}`, `packages/workspace/workspace`, `packages/deliverables/deliverable-file-references`, `packages/preset/persona` (a per-session persona segment).
- **Questions this chapter answers**: where each prompt segment comes from; how multiple plugins write into one prompt without stepping on each other; why tool schemas are not prompt text; whether `context()` and context plugins are the same mechanism.
- **New teaching code in this chapter** (`ch08/code/`): `system_prompt.py`, `prompt_agent.py`, `context_plugins.py`, `main.py`, `bad_example.py`.

## 9. scope Scoped Registration

- **Topic**: how different agents in the same process isolate their respective capabilities.
- **New mechanisms**: `ScopedLayers` (global layer + per-agent overlay + lineage chain) + shadowing + restriction intersected along the chain + per-Agent scope (a scope minted per Agent; the scope is the unit of unloading).
- **Corresponding source modules**: `packages/core/scope` (`index.ts`, `store.ts`); consumer `packages/core/tools`; isomorphic use `packages/core/system-prompt`; per-Agent scope instance `packages/experimental/browser-use-runtime` (see Chapter 13).
- **Questions this chapter answers**: how tool registrations from coexisting agents avoid cross-contamination; which same-name tool wins; why a child agent sees its parent's registrations; how to hide one tool from one agent only.
- **New teaching code in this chapter** (`ch09/code/`): `scope.py`, `main.py`, `bad_example.py`.

## 10. compaction and Token Pressure

- **Topic**: what to do when the context grows too long.
- **New mechanisms**: the `compaction` seam (checkpoint / region / tool-pairing) + tool-result-pruner + image-offload + token-meter triggering; surface folding and incremental snapshots.
- **Corresponding source modules**: `packages/compaction/{compaction,compaction-basic,compaction-tool-result-pruner,compaction-image-offload}`, `packages/core/session/src/surface.ts`, `packages/llm/token-meter`.
- **Questions this chapter answers**: what happens when cumulative tokens hit the window limit; how compaction lets "the log is complete" and "the model sees only a window" both hold; why compaction cannot start at an arbitrary position.
- **New teaching code in this chapter** (`ch10/code/`): `compaction.py`, `surface.py`, `main.py`, `bad_example.py`.

## 11. subagent Delegation

- **Topic**: delegating tasks to subagents for parallel/isolated execution.
- **New mechanisms**: `ctx.subagents` seam + multiple transport providers (in-process spawn / fork, acp, codex, claude-code, dsh-sdk) + delegation depth and lineage + `tool-subagent`/`tool-subagent-control` + the Team shared task board (whole snapshot + revision CAS + TeamJournal).
- **Corresponding source modules**: `packages/subagent/{subagent,subagent-spawn-in-process,subagent-fork-in-process,tool-subagent,tool-subagent-control}`, `packages/team/{agent-team,tool-agent-team}`.
- **Questions this chapter answers**: why a subtask runs in its own session; how multiple delegation styles share one entry; how the model triggers delegation and keeps it continuable; why Team's shared task board creates no agents and schedules nothing.
- **New teaching code in this chapter** (`ch11/code/`): `subagent_runtime.py`, `providers.py`, `tool_subagent.py`, `task_board.py` (CAS + cycle detection + projection), `main.py`, `bad_example.py`.

## 12. skill Loading

- **Topic**: pluggable packages of specialized capability.
- **New mechanisms**: the `ctx.skills` provider registry (multi-source merging, rank ladder, near-layer shadowing) + on-demand catalog messages (catalog replacement rendering) + an experimental provider (`experimental/skill-badge`).
- **Corresponding source modules**: `packages/skill/{skill,skill-filesystem,skill-office,tool-skill,tool-workspace-dependencies}`, `packages/experimental/skill-badge`.
- **Questions this chapter answers**: why skill bodies cannot be welded into the system prompt; how a seam hosts several sources at once; how the model loads one skill's body on demand.
- **New teaching code in this chapter** (`ch12/code/`): `skill_registry.py`, `skill_providers.py`, `tool_skill.py`, `main.py`, `bad_example.py`.

## 13. web / lsp and Exclusive Capabilities: Who Serves Is Decided at Execution Time

- **Topic**: external capabilities enter through registries, and who is selected, at what moment.
- **New mechanisms**: `ctx.web` (pick one at execution time, then normalize) + `ctx.lsp` (route by extension dimension, atomic registration, a closed union of four operations) + single-slot exclusive registries (`ctx.computerUse`/`ctx.browserUse`: admission only, at composition time) + MCP client mounting.
- **Corresponding source modules**: `packages/web/{web,web-search-deepseek,web-fetch-http,tool-web}`, `packages/lsp/{lsp,lsp-stdio,tool-lsp}`, `packages/computer-use/computer-use`, `packages/browser-use/browser-use`, `packages/experimental/{cua,computer-use-cua-driver-mcp,browser-use-runtime,browser-use-playwright,browser-use-chrome-devtools,browser-use-stagehand}`.
- **Questions this chapter answers**: why search/fetch cannot hard-wire an engine call; how web's execution-time selection differs from lsp's dimension routing; what a registry for a globally exclusive capability (one desktop, one browser) still does; who is selected, and when.
- **New teaching code in this chapter** (`ch13/code/`): `web_runtime.py`, `web_providers.py`, `lsp_runtime.py`, `lsp_providers.py`, `tool_web.py`, `tool_lsp.py`, `main.py`, `bad_example.py`.
- **Note**: the §5 single-slot exclusive registry is 48 lines, explained inline; no new teaching file.

## 14. interaction / permission / goal / plan

- **Topic**: how humans step in, grant authorization, and co-plan.
- **New mechanisms**: the approval seam (request/decide/effectivePolicy/setPolicy) + permission-presets + commands + user-questions + the goal domain (drive/reservation/commit) + plan-mode.
- **Corresponding source modules**: `packages/interaction/{user-approval,user-questions,permission-presets,commands}`, `packages/goal/{goal,goal-round-driver}`, `packages/plan/plan-mode`, `packages/core/tools/src/pipeline.ts` (where the ask branch lands).
- **Questions this chapter answers**: who the pipeline's ask branch is wired to in HEAD; which calls need a human and who decides; how a goal survives compaction across turns.
- **New teaching code in this chapter** (`ch14/code/`): `approval.py`, `ask_gate.py`, `questions.py`, `presets.py`, `commands.py`, `goal.py`, `goal_round.py`, `plan_mode.py`, `main.py`, `bad_example.py`.

## 15. preset / bundle / profile Composition

- **Topic**: assembling loose parts into reusable product forms.
- **New mechanisms**: declarative preset (one plugin row, not a directory scan) + each Agent bound to the revision current at its creation (`retain` returns one Generation; a new revision does not disturb old agents) + the bundle patch layer + the profile stack + opt-in experimental bundles.
- **Corresponding source modules**: `packages/preset/{agent-preset,agent-preset-registry,persona}`, `packages/bundle/{base,headless,web-app}`, `packages/boot/app-boot`, `packages/util/official-bundles`, `packages/experimental/{cua-bundle,ralph-bundle,session-titles-bundle}`.
- **Questions this chapter answers**: how one model-facing plugin combination is reused across sessions without an upgrade interrupting them; how the startup list is customized incrementally per product form; what preset / bundle / profile each own; why this `preset` is not Chapter 14's `permission-presets`.
- **New teaching code in this chapter** (`ch15/code/`): `preset.py` (revision/Generation edition), `bundle.py`, `patch.py`, `profile.py`, `main.py`, `bad_example.py`.

## 16. typert / api / sdk — The Cross-Process Typed RPC Runtime

- **Topic**: the typed cross-process RPC runtime.
- **New mechanisms**: the typert type graph and InvocationDescriptor + the RPC gateway's two validation paths (strict / src-json) + the JSON-RPC stdio SDK + `TypertRemoteService` and `@Remote` export names + ACP.
- **Corresponding source modules**: `packages/typert/{protocol,registry,generator,loader}`, `packages/api/{gateway,remotes,workspace-files}`, `packages/sdk/{protocol,client,server}`, `packages/acp/acp`, `packages/document/office-to-pdf` (`@Remote render` + `@Remote('generation')`).
- **Questions this chapter answers**: how out-of-process consumers call capabilities inside the host process; how a type contract crosses a process and is validated at the boundary; why a new exported method needs no hand-written route or client stub; what `typert`, the gateway, the SDK, and ACP each do.
- **New teaching code in this chapter** (`ch16/code/`): `typert.py`, `gateway.py`, `sdk.py`, `main.py`, `bad_example.py`.
- **Boundary**: `api/session-controller` and `host/apiproxy` are host-side BFFs (they reorganize host semantics per client); this tutorial does not expand them.

## 17. workflow / PTC runtime / python end-to-end

- **Topic**: integrating all mechanisms into the full startup chain and end-to-end flow.
- **New mechanisms**: the `ctx.workflowEngine` seam (a four-stage skeleton — validate → submit → event stream → result — with the script handed to the PTC runtime) + Ralph (demoted to an opt-in experimental bundle) + one launcher and four shipped profiles + the Python SDK end to end.
- **Corresponding source modules**:
  - `packages/workflow/{workflow,workflow-ptc}` (`WorkflowExecution` = `vm.Context` + `vm.Script`).
  - `packages/ptc-runtime/{ptc-runtime,ptc-runtime-node}` (`ctx.ptcRuntime`, the runtime host for Chapter 4's executor).
  - `packages/experimental/{tool-ralph,ralph-bundle}` (not enabled in the default combination).
  - `packages/boot/app-boot`, `apps/cli` (`bin.ts` / `profile-boot.ts`), `packages/util/official-bundles`, `packages/bundle/base`.
  - `python/sdk` (`api.py` / `client.py`), `packages/sdk/server` (the server side of the same JSON-RPC protocol).
- **Questions this chapter answers**: why the multi-agent workflow engine is still "just a seam"; what Ralph gains by opening a fresh child agent every round; which services a request crosses from Python SDK construction to the model's first token.
- **New teaching code in this chapter** (`ch17/code/`): `workflow.py` (four-stage), `ralph.py` (marked opt-in), `boot.py`, `sdk.py`, `main.py`.
- **Boundary**: the `apps/web` browser front end and the `packages/host` / `packages/client` client assembly are not expanded here; this chapter covers the startup chain and the execution host only.

## 18. The Telemetry Channel — One Empty Service, Two Queues, Three Ownerships

- **Topic**: after the system runs, how events leave the process. The cross-cutting close: observing the system Chapter 17 brought up; main-line integration was completed in Chapter 17, so this chapter does not claim to be an integration chapter.
- **New mechanisms**: an empty-mount `ctx.otel` (creates no connection, queue, or identity; only two factories) + two channels with two queueing philosophies (product events batched by count / session logs batched through a byte gate that rejects an over-limit single record outright, single in flight) + authorization and withholding on the consumer side (default `FEEDBACK_ONLY`, a missing `sourceEvent` means refusal, `eventAt(seq)` identity check) + shutdown ownership (the consumer calls `shutdown(signal)` in its own disposal with an outer deadline; on expiry `stopPending()` drops the queue).
- **Corresponding source modules**:
  - `packages/telemetry/otel` (`index.ts`, `event-log.ts`, `event-transport.ts`, `session-log.ts`): the service definition and both channels.
  - `packages/host/product-telemetry-otel`: the second host-level consumer and the evidence for the "global face" (`inject=['otel']`).
  - `packages/session/session-telemetry-otel` (authorization side) + `packages/session/session-telemetry` (capture side, Chapter 3).
- **Questions this chapter answers**: what the telemetry service actually creates when mounted, and why the answer is "nothing"; why product events and session logs must travel two channels and how their queueing rules differ; who decides whether a session event may leave; since channels belong to consumers, who drains on shutdown and who drops after the deadline.
- **New teaching code in this chapter** (`ch18/code/`, standard library only, reusing `Context`/`Service` from `ch01/code/cordis.py`):
  - `otel.py`: the empty-mount `OTel(Service)` + the `create_event_reporter` / `create_session_log_reporter` factories.
  - `channels.py`: `CountBatcher` (by count) + `ByteGateProcessor` (by bytes, single-record rejection, single in flight) + an in-memory `Sink` with injectable failure/timeout.
  - `backends.py`: two consumer adapters (feedback authorization + `sourceEvent` identity + deadline shutdown / ordinary events).
  - `main.py`: six demonstration segments, each printing its assertions.
  - `bad_example.py`: singleton provider + one shared queue + no owner on shutdown — three pits.

---

## Progressiveness Self-Check List

- [x] Chapter 1 establishes the container kernel (everything is a plugin, hello.py closed loop).
- [x] Chapter 2 establishes the minimal runnable core (agent-loop closed loop, no tools, no persistence).
- [x] From Chapter 3 on, each chapter adds exactly one mechanism cluster.
- [x] Chapter 17 integrates all mechanisms into the end-to-end startup chain (the main-line integration chapter).
- [x] Chapter 18 closes from a cross-cutting angle: it observes the system Chapter 17 brought up, and does not claim to be an integration chapter.
- [x] Each chapter spells out: chapter number, topic, new mechanisms, source modules, questions answered.
- [x] 18 chapters in total; every "Corresponding source modules" line follows the package paths of upstream HEAD `8bd0733`.

---

## Terminology Naming Table

> Naming discipline: one concept, one Chinese name; the "English / source symbol" column gives the name you can grep for in code. Only project-specific concepts are included; generic programming concepts (queues, TLS, PIDs, …) are excluded. "First appears" is the chapter where the body first defines the term.

| Term | English / source symbol | One-line definition | First appears |
|---|---|---|---|
| session format chain v0→v4 | `session-format*` (six packages) | One adjacent migration edge per version; the catalog validates at startup that the chain has no gaps; each physical row is decoded once. | 03 |
| adjacent migration edge | `defineSessionFormatMigration` (`session-format/src/chain.ts`) | A migration declaration that only allows `vN → v(N+1)`; a cross-version step must be split into several, locked in at declaration time. | 03 |
| live write path | the backend (not an in-session buffer) owns the write channel | Events on the `Context` bus are routed by session id into the active handle's batching window; the writer is the handle, and there is only one. | 03 |
| SessionHandle | `session-persistence/src/handle.ts` | The per-session read/write handle returned by `create/open` (read/append/flush/close); the only channel for log reads and writes. | 03 |
| handle ownership contract | `session-persistence/src/errors.ts` (seven error classes) | One session has at most one writing handle at a time; reopening reports AlreadyOwned, losing it reports OwnershipLost. | 03 |
| durability barrier (flush) | `SessionHandle.flush` | Persists already-appended events and returns confirmation; without the return, nothing counts as persisted. | 03 |
| storage domain | `packages/storage/*` | The key-value domain for non-session application data (workspace records, session sidecars); session logs do not go here. | 03 |
| FEEDBACK_ONLY | `session-telemetry-otel` enum | The telemetry default: only events the user explicitly gave feedback on are authorized for upload (the other mode is `DISABLED`). | 03 |
| sourceEvent | `SessionTelemetryRecord` | The reference to the source event kept in a telemetry record; redaction deleting the source withholds the record. | 03 |
| identity check | `eventAt(seq)` comparison (`!==`) | An authorized event must be the very same object at the same canonical-log position; a replayed copy authorizes no prefix. | 03 |
| PTC (programmatic tool calls) | `packages/ptc-runtime/*`; formerly Code Mode | The model emits a program instead of direct tool calls, calling tools indirectly through a generated SDK. | 04 |
| run_code | the only tool exposed by `tool-executor-ptc` | The sole tool visible to the model in PTC mode: submit a program, and the executor runs it. | 04 |
| tool executor | `core/tools/src/executor.ts`, `tool-executor-{native,ptc}` | A seam parallel to the tool registry, answering "how tools are presented to the model + how a batch runs"; native and PTC are two implementations. | 04 |
| native executor (native) | `packages/core/tool-executor-native` | Projects every visible tool into one schema and hands the whole batch to the shared `executeToolBatch`. | 04 |
| PTC executor | `packages/core/tool-executor-ptc` | Exposes only `run_code`; the rest are rendered as SDK text, and nested calls still go through the same guard pipeline. | 04 |
| nearest-scope executor selection | `ToolsService` + executor registration | One executor per scope; the nearest on the scope chain wins; a duplicate in the same scope fails immediately. | 04 |
| agent-tool-presentation (deprecated) | `packages/core/agent-tool-presentation` | The old native/code/both selector row; deprecated in favor of an executor row, with `config.mode` removed. | 04 |
| ToolView (tool view) | the result of scope-chain merge + restriction | All tools one scope "can see"; the presentation face reads from it, never from the raw registry. | 04 |
| restriction (tool mask) | `ToolRestriction` (`contracts.ts`) | A per-scope allow/deny list that filters only inherited global tools, never the scope's own registrations. | 04 |
| present's two-step | `packages/deliverables/tool-present` | Stash the declaration during execution; append `deliverables/presented` only after `tools/result` succeeds. | 04 |
| deliverables/presented | `tool-present` | The persistent event declaring "which files this turn delivered"; a later failure of the enclosing program does not revoke it. | 04 |
| whole-provider replacement | the SSH provider family (Chapter 6) | Replacing not one implementation but a whole family of providers, with definition and consumer untouched. | 05 |
| descriptive fields (descriptor) | `ctx.ptcRuntime`'s `language`/`isolation` | Read-only descriptor fields a definition layer exposes alongside its abstract methods, used only for diagnostics and selection; this is not a new seam shape and stays method-call style. | 05 |
| SSH helper | `packages/ssh/ssh/src/helper.ts` | A real dsh process running on the remote side, installing local fs/subprocess/sandbox providers there. | 06 |
| dual channel (management / data) | `SshConnection` + `SshRpcPeer` | One master carries management RPC; each program stream opens its own channel, so they never contend for a window. | 06 |
| ptcLaunch | `packages/ssh/ssh/src/index.ts` | The entry that hands a remote executable to the PTC runtime (used in Chapter 17). | 06 |
| intent waterfall | `fs/write-intent` / `edit-intent` / `delete-intent` | A pluggable interception point before a file changes, used to capture the original content whole. | 06 |
| TurnRecorder | `workspace-changes/src/recorder.ts` | The recorder that snapshots a baseline tree at turn start, captures before each change, and emits a diff at turn-stopping. | 06 |
| workspace/changes | `workspace-changes/src/types.ts` | An address-only event carrying just a turn number; the summary and diff stay in the Host, fetched by (sessionId, seq). | 06 |
| SettingsForms | `packages/settings/settings` | The editing face that projects active plugins' Config into editable forms with revision-conflict checks; not a key-value store, and not the read path for config. | 07 |
| per-Agent scope | `createScope(ctx, agent)` | A scope whose lifetime is bound to one Agent instance; resources live inside, and unloading the Agent unloads them. | 09 |
| task board | `TeamTaskBoard` | A task DAG hung on the tree-root session log: whole-snapshot commits plus revision CAS. | 11 |
| claim / reassign / blockedBy | `team/agent-team/src/task-board.ts` | The three task elements: claiming, reassigning (Lead only), dependency edges (must be acyclic). | 11 |
| TeamJournal | `team/agent-team/src/journal.ts` | One serial transaction queue per Lead; a `team/task` append to the root session plus flush is the confirmation. | 11 |
| single-slot exclusive registry | `ComputerUseRegistry` / `BrowserUseRegistry` | A globally exclusive capability admits one provider; registration returns a disposer, and a second registration (even same-named) throws. | 13 |
| ProviderName brand | `brand.ts` | A type-level name marker not validated at runtime, used only to tag diagnostic information with its origin. | 13 |
| SessionResources | `experimental/browser-use-runtime` | A browser-resource ownership table keyed by live Agent instances; abort in-flight work first, then await release. | 13 |
| empty-definition seam | `ctx.computerUse` / `ctx.browserUse` / `ctx.otel` | A definition layer declaring no abstract members and carrying no config or defaults; it may have concrete factories or registration methods, with all semantics on the consumer side. | 13 |
| revision (of a preset) | `AgentPresetRegistry` | +1 on every definition change; an Agent binds the revision current at its creation and is not reloaded by a newer one. | 15 |
| Generation / retain | `agent-preset-registry/src/index.ts` | `retain(id)` materializes and returns one generation of the combination; old generations keep serving existing Agents. | 15 |
| declarative preset | profile YAML / bundle patch + `AgentPreset` | A preset is a declaration row, not the product of a directory scan; `$DSH_HOME/.agent-presets` is no longer read. | 15 |
| opt-in experimental bundle | `experimental/{ralph,cua,session-titles}-bundle` | A bundle not enabled in the default combination and mounted explicitly. | 15 |
| TypertRemoteService | `typert/protocol/src/index.ts` | A service base class: registers the Cordis service on construction and attaches the `typertRemote` binding so `@Remote` methods are discoverable by the gateway. | 16 |
| Remote export name | `@Remote('generation')` | The `@Remote` overload: with no argument the method name is the endpoint name; with a string it swaps in an export name (`getGeneration` → `officeToPdf/generation`). | 16 |
| officeToPdf / generation | `packages/document/office-to-pdf` | A bounded Office→PDF conversion service; a config swap invalidates a whole generation of cache, and browsers judge their cache by generation. | 16 |
| bounded admission | `ConversionQueue` | Foreground/background quota tiers + reader cap + content-addressed cache; bytes are reserved before reading. | 16 |
| WorkflowExecution | `workflow-ptc/src/runtime.ts` | The workflow-script entity compiled and executed inside the PTC Node runtime (`vm.Context` + `vm.Script`). | 17 |
| receipt | the return value of `session/query` (`api.py` / `client.py`) | The token a Python SDK input comes back with: the prompt's messageId or a command's `CommandExecution`; one run = one receipt + one notification stream. | 17 |
| OTel channel (reporter) | `ctx.otel` + `create*Reporter` | The outbound event channel a consumer gets from a factory, with its own exporter/queue/scope. | 18 |
| count-based batching | `CountBatcher` / SDK `BatchLogRecordProcessor` | The ordinary-event queueing policy: send a batch once enough records accumulate; drop new records when full. | 18 |
| byte gate | `SessionLogProcessor` | The session-log queueing policy: pack by bytes, reject a single over-limit record outright without truncation, and keep at most one request in flight. | 18 |
| single in flight | serial request constraint | At most one transport request on the wire; the next starts only after the previous request and its `forceFlush` complete. | 18 |
| global face | `packages/host/product-telemetry-otel` | The fact that two mutually unaware host-level consumers share one `ctx.otel`. | 18 |
| channel ownership (shutdown signal) | `shutdown(signal)` + fiber disposal | Whoever creates the channel closes it: in the consumer's own disposal, with a deadline, dropping the queue on expiry. | 18 |
| stopPending | `SessionLogProcessor` | On deadline expiry, empties queued records and prevents new requests; in-flight requests may still settle. | 18 |
