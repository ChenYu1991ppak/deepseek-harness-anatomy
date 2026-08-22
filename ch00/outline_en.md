# DeepSeek Harness (dsh) Source-Study Outline

> Progressive chapter outline: each chapter adds one new mechanism on top of the previous one; the final chapter integrates all mechanisms into an end-to-end flow.
> Terms are defined at their first appearance within a chapter; a "Terminology Naming Table" is appended at the end for cross-chapter consistency reference.
> Body text in Simplified Chinese, code/symbols in English, diagrams in Mermaid.
> Teaching code: `chNN/code/` accumulates incrementally per chapter (one self-contained directory per chapter).

## 0. Overview

- **Worldview**: everything is a plugin; agent = model + harness; registration is effect; the seam triple-role.
- **Reading path**: read the vendored Cordis primitives first → the minimal agent-loop closed loop → thicken mechanism by mechanism.
- **Reader's starting point**: no need to read the whole repository first; build the mental model chapter by chapter along the outline.

## 1. The Cordis Container Kernel (Everything Is a Plugin)

- **Topic**: how the plugin container is assembled (the minimal core of context/services/events).
- **New mechanisms**: `Context` service container + `Service` + plugin registration + `inject` dependency checking + `Fiber`/`effect` reversible registration + `on`/`emit`/`serial`/`waterfall` event dispatch.
- **Corresponding source modules**: `@cordisjs/core` (context/service/registry/events/fiber/reflect).
- **Questions this chapter answers**: what are context, service, reversible registration, and event dispatch.
- **New teaching code in this chapter** (`ch01/code/`, self-contained, standard library only, runnable via `python ch01/code/hello.py`):
  - `cordis.py`: teaching edition of the Cordis kernel.
  - `hello.py`: the minimal runnable example in §2.
  - `bad_example.py`: the counter-example in §1.

## 2. The Minimal agent-loop Closed Loop (One Message In, One Reply Out)

- **Topic**: how a user message becomes a reply — the minimal runnable closed loop.
- **New mechanisms**: `Session` (in-memory append-only) + `SystemPromptService` (+`render_prompt`) + `AgentLoop` (kick/turn/step).
- **Corresponding source modules**: `@cordisjs/agent-loop` + `@cordisjs/core` Session + `@cordisjs/system-prompt` + llm stub.
- **Questions this chapter answers**: how an agent gets "assembled".
- **New teaching code in this chapter** (`ch02/code/`, incrementally reusing `cordis.py` from ch01, runnable via `python ch02/code/main.py`):
  - `agent_loop.py`: teaching edition of the agent-loop.
  - `main.py`: runnable assembly entry.

## 3. The session Event Stream and Persistence Projection

- **Topic**: how conversations are recorded, persisted, and replayed.
- **New mechanisms**: `SessionEvent` append log + `sessionPersistence` seam (JSONL/SQLite) + `sessionProjection` cold reads and cache.
- **Corresponding source modules**: `packages/session/session-persistence`, `session-persistence-jsonl`, `session-persistence-sqlite`, `session-projection`, `session-projection-cache`, `session-title`, `session-telemetry`.
- **New teaching code in this chapter** (`ch03/code/`, incrementally reusing `cordis.py` from ch01 and `agent_loop.py` from ch02, runnable via `python ch03/code/main.py`):
  - `session_persistence.py`: teaching edition of session persistence and projection (seam dual contracts / write-behind / coordinator / JSONL and SQLite dual backends / projection registry and cache / title and telemetry).
  - `main.py`: runnable assembly entry (8-stage closed loop).
  - `bad_example.py`: the counter-example in §1.

## 4. tools Registration and Execution Pipeline

- **Topic**: how the model acquires and invokes tools.
- **New mechanisms**: `ctx.tools` scoped registration + tool schema injection into the system-prompt + guard execution pipeline (pre-execute/execute/post-execute).
- **Corresponding source modules**: `packages/core/tools`, `packages/guard`.

## 5. The Triple Role of the capability seam

- **Topic**: how one capability is decomposed into "definition, implementation, consumption".
- **New mechanisms**: the three roles of Service Definition / Service Provider / Consumer + the full seam contract (using shell as the example).
- **Corresponding source modules**: `packages/shell/shell`, `shell/bash-local`, `shell/tool-bash`; `docs/capability-seams.md`.

## 6. The subprocess / shell / fs Execution World

- **Topic**: the execution surface that lets the agent actually do things on the machine.
- **New mechanisms**: `ctx.subprocess` process tree + `ctx.shell` + `ctx.fs` + `ctx.sandbox` isolation, wholesale provider migration.
- **Corresponding source modules**: `packages/subprocess`, `packages/shell`, `packages/fs`, `packages/sandbox`, `native/landlock-run`.

## 7. LLM Adaptation and Streaming

- **Topic**: how the model layer is swapped and how streaming output works.
- **New mechanisms**: `ctx.llm` adapter seam + `assistant/chunk` streaming + `credentials`/`settings` seam.
- **Corresponding source modules**: `packages/llm/llm`, `llm-deepseek`, `llm-pi-ai`, `token-meter`; `packages/credentials`, `packages/settings`.

## 8. system-prompt Assembly and context

- **Topic**: where the "prompt" sent to the model comes from.
- **New mechanisms**: prompt section registration/merging + workspace instructions + time context + `session-reference`.
- **Corresponding source modules**: `packages/core/system-prompt`, `packages/context`, `packages/workspace`.

## 9. scope Scoped Registration

- **Topic**: how different agents in the same process isolate their respective capabilities.
- **New mechanisms**: per-agent scoped registration + shadowing + restriction + lineage.
- **Corresponding source modules**: `packages/core/scope`.

## 10. compaction and Token Pressure

- **Topic**: what to do when the context grows too long.
- **New mechanisms**: `compaction` seam + tool-result-pruner + token-meter triggering.
- **Corresponding source modules**: `packages/compaction`.

## 11. subagent Delegation

- **Topic**: delegating tasks to subagents for parallel/isolated execution.
- **New mechanisms**: `ctx.subagents` seam + multiple transport providers (spawn/fork/acp/codex/claude-code/dsh-sdk) + `tool-subagent`/`tool-subagent-control`.
- **Corresponding source modules**: `packages/subagent`.

## 12. skill Loading

- **Topic**: pluggable packages of specialized capability.
- **New mechanisms**: `ctx.skills` provider registry + skill-badge/filesystem + `tool-skill` catalog and loading.
- **Corresponding source modules**: `packages/skill`.

## 13. web / lsp Capabilities

- **Topic**: the two major external capabilities — retrieval and code semantics.
- **New mechanisms**: `ctx.web` (search/fetch) + `ctx.lsp` (normalized four-operation queries).
- **Corresponding source modules**: `packages/web`, `packages/lsp`.

## 14. interaction / permission / goal / plan

- **Topic**: how humans step in, grant authorization, and co-plan.
- **New mechanisms**: approval seam + permission-presets + commands + goal domain + plan-mode.
- **Corresponding source modules**: `packages/interaction`, `packages/goal`, `packages/plan`.

## 15. preset / bundle / profile Composition

- **Topic**: assembling loose parts into reusable product forms.
- **New mechanisms**: agent-presets per-session composition + bundle patch layer + profile stack.
- **Corresponding source modules**: `packages/preset`, `packages/bundle`, `packages/boot`.

## 16. typert / api / sdk

- **Topic**: the typed cross-process RPC runtime.
- **New mechanisms**: typert type graph + RPC gateway + JSON-RPC sdk + ACP.
- **Corresponding source modules**: `packages/typert`, `packages/api`, `packages/sdk`, `packages/acp`.

## 17. workflow / Ralph / python + apps-cli-web End-to-End

- **Topic**: integrating all mechanisms into the full startup chain and end-to-end flow.
- **New mechanisms**: workflow engine + Ralph loop + Python SDK/runtime + apps entry (host/client) integration.
- **Corresponding source modules**: `packages/workflow`, `python/sdk`, `python/sdk-runtime`, `apps/cli`, `apps/web`, `packages/host`, `packages/client`.

---

## Progressiveness Self-Check List

- [x] Chapter 1 establishes the container kernel (everything is a plugin, hello.py closed loop).
- [x] Chapter 2 establishes the minimal runnable core (agent-loop closed loop, no tools, no persistence).
- [x] From Chapter 3 on, each chapter adds exactly one mechanism cluster.
- [x] The final chapter (17) integrates all mechanisms into the end-to-end startup chain.
- [x] Each chapter spells out: chapter number, topic, new mechanisms, source modules.
