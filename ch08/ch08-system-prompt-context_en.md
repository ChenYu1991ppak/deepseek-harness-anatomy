# Chapter 8: system-prompt Assembly and context: Where Does the Prompt Sent to the Model Come From

> The prompt is not a string; it is an assembled artifact.

## Questions this chapter answers

- Where does each section of the system prompt come from? How do multiple plugins write into the same prompt without stepping on each other?
- Why isn't the tool schema inside the prompt text? What is its relationship to the prompt?
- How do dynamic pieces of information like workspace instructions, the current time, and cross-session references enter the model's field of view?
- Both `context()` and the context plugins are called "context" — are they the same mechanism?

Chapter 7 turned the model seat into a pluggable runtime, and at the end of the chapter (§8) it left a more fundamental question: where exactly does the prompt sent to the model come from, and how is it assembled? This chapter answers that question.

Chapter 4 "tools registration and execution pipeline" solved the tool-side problem: plugins register tools via `ctx.tools.register()`, `wireSchemas()` renders the registry into schemas and hands them to the model, and after the model returns `tool_use`, it lands back in the session through the execution pipeline. That chapter left one loose end: ch04's `main.py` stuffed the schema text into an ordinary section of Chapter 2's stub `SystemPromptService` (`ch04/code/main.py:74`), and the comment in `tools.py` already said at the time that "the real code goes through `ctx.systemPrompt.tools(provider)`" — where the real landing point is, and why it isn't a section, is exactly what this chapter answers.

Going back one more step: Chapter 2 §4 gave a minimal stub of `SystemPromptService` (`ch02/code/agent_loop.py:145`), with only two methods, `section()` and `assemble()`, enough to run Chapter 2's loop, but not enough to hold all the semantics of the real project. This chapter upgrades that stub to the full version.

This chapter's code is built incrementally on top of ch04:

| Source | File | How this chapter uses it |
| --- | --- | --- |
| Chapter 1 | `ch01/cordis.py` | Reused via sys.path: `Context`, `Service`, `ctx.effect` (the source of registration reversibility) |
| Chapter 2 | `ch02/agent_loop.py` | Reused via sys.path: `Sessions`, `ReactLoopAgent` (this chapter inherits from it and replaces `pre_step`/`step`), `create_assistant_message` (used by `step` to assemble the assistant reply) |
| Chapter 4 | `ch04/tools.py` | Reused via sys.path: `ToolRuntime`, `ToolDefinition` (`wire_schemas` finds its real landing point in this chapter) |
| Newly added in this chapter | `ch08/system_prompt.py` | Full version of the system-prompt service, replacing ch02's stub (without modifying ch02's files) |
| Newly added in this chapter | `ch08/context_plugins.py` | Three context plugins: workspace instructions, time, cross-session references |
| Newly added in this chapter | `ch08/prompt_agent.py` | Loop-side glue: context snapshot projection + full-version `pre_step` |

One-sentence main line: **plugins register ordered sections via `ctx.systemPrompt.section()`, `assemble()` merges and sorts, `renderPrompt()` renders into system text; tool schemas are an independent field parallel to the text; workspace instructions, time context, and cross-session references are injected by context plugins as user messages.**

## 1. The System Prompt Is a Hard-Coded Giant String

First look at the anti-pattern. Suppose every feature module concatenates its own requirements directly into one global string:

```python
SYSTEM_PROMPT = "You are an AI assistant."  # the only initial text


def register_safety(prompt):
    """Safety module: concatenated directly at the end; position is determined by registration order."""
    return prompt + "\n\n[Safety policy] Do not leak sensitive information."


def register_persona(prompt):
    """Persona setting: should originally be at the front, but can only be concatenated at the end."""
    return prompt + "\n\n[Persona] You are a rigorous code reviewer."


def register_tools(prompt):
    """Tool schema: also mixed into the text; what the model reads is a JSON string."""
    schema = {"name": "read_file", "parameters": {"path": "string"}}
    return prompt + f"\n\n[Tools] {schema}"
```

Run `python3 ch08/code/bad_example.py`, output:

```text
Final prompt (note: the persona setting should originally be at the front, but actually ends up last):
You are an AI assistant.

[Safety policy] Do not leak sensitive information.

[Persona] You are a rigorous code reviewer.

[Tools] {'name': 'read_file', 'parameters': {'path': 'string'}}

Suppose the safety module is now unloaded...
The giant string still contains the safety policy: True

After the safety module is registered twice, the number of occurrences of the safety policy: 2
```

All three problems come from the premise that "the prompt is a string":

1. **Order out of control**: a section's position is determined by concatenation order, not by importance. The persona setting should originally be at the front, but actually ends up last.
2. **Irreversibility**: once concatenated into the string, it cannot be taken out. Even if the safety module is unloaded, its text remains welded into the prompt; duplicate registration directly causes content duplication, with no way to detect it.
3. **Structural collapse**: tool schemas are mixed into the text; what the model reads is a JSON string, not a field that callers can process structurally.

The real project's solution is to upgrade the prompt from a "string" to a "registry + assembly flow": each section is one **revocable registration**, order is determined by an explicit `order`, and assembly happens fresh every time it is needed. First look at the overall structure, then break down each mechanism.

## 2. Overall Structure: One Assembly Line, Two Injection Paths

First look at the whole picture, then break down the parts. The system-prompt package is one assembly line, and `PromptAssembly` is its product; dynamic information enters the model's field of view via two mutually different paths:

```mermaid
flowchart TB
    subgraph RegistrationSide["Registration side (plugins)"]
        P1["Plugin A<br/>section(name, text, order)"]
        P2["Plugin B<br/>section(name, text, order)"]
        P3["Tool runtime<br/>tools(provider)"]
        P4["Context provider<br/>context(name, text, order)"]
    end

    subgraph AssemblyLine["system-prompt service"]
        L["Layered registry<br/>global layer + scope layer"]
        A["assemble()<br/>merge → sort → evaluate"]
        AS["PromptAssembly<br/>sections / contexts / tools / variables"]
    end

    subgraph Exits["Two exits"]
        R1["renderPrompt()<br/>system text"]
        R2["renderContextSnapshot()<br/>context snapshot (mechanism A)"]
        T["assembly.tools<br/>parallel field"]
    end

    subgraph MechanismB["Mechanism B: context plugins"]
        C1["agent-instructions<br/>pre-step injection"]
        C2["time-context<br/>pre-step injection"]
        C3["session-reference<br/>injection before enqueue"]
    end

    P1 --> L
    P2 --> L
    P3 --> A
    P4 --> L
    L --> A
    A --> AS
    AS --> R1
    AS --> R2
    AS --> T
    C1 --> M["Model call<br/>user messages inside messages"]
    C2 --> M
    C3 --> M
    R2 --> M
    R1 --> S["Model call<br/>system parameter"]
    T --> S
```

Correspondence between the diagram and the body:

- **Registration side**: plugins don't write strings; they only call registration APIs. `section()` registers system text sections, `context()` registers runtime context sections, and `tools()` registers tool schema providers. Every registration returns a revocable disposer.
- **Assembly line**: section and context registrations enter the layered registry (this chapter uses only the global layer; the minimal form of the scope layer shadowing the global layer is in §3, with the full expansion in Chapter 9); tools providers do not enter the layered registry and go directly into `assemble()`'s evaluation queue (P3→A in the diagram). `assemble()` merges the registry, sorts by `order`, evaluates dynamic text and tools providers, and produces `PromptAssembly`.
- **Two exits**: `renderPrompt()` renders sections into system text; `renderContextSnapshot()` renders contexts into a snapshot (**mechanism A**); `assembly.tools` is an independent field parallel to both and does not enter any text.
- **Mechanism B**: the three context plugins do not go through the assembly line; each injects dynamic information as user messages — two hook the `agent/pre-step` event, and one is resolved by the host before enqueue. The distinction between mechanism A and mechanism B is a key concept of this chapter, compared specifically in §6.4.

## 3. section Registration and assemble Assembly

### 3.1 Concept introduction

**PromptSection (section contribution)**: the input of one registration, four elements — `name` (section name, unique within the same layer), `order` (sort key, concatenated in ascending order), `text` (static text or dynamic provider), `scope` (the scope layer it belongs to; this chapter uses only the global layer). Corresponds to source code `packages/core/system-prompt/src/index.ts:53`.

**order (sort key)**: a section's position in the final text is determined solely by order and has nothing to do with registration order. The real project's conventions: the harness identity section is fixed at `-100` (always first), the persona section is fixed at `0`, and tool guidance sections fall in the `100-199` range. Corresponds to `PERSONA_ORDER` at `index.ts:131` and the identity section registration at `index.ts:358`.

**disposer (unregistration function)**: the return value of `section()`. Registration happens via `ctx.effect` (Chapter 1's reversible side effect); calling the disposer unregisters that section. This is the direct antidote to problem 2's "irreversibility".

**PromptAssembly (assembly product)**: the return value of `assemble()`, four fields — `sections`, `contexts`, `tools`, `variables`. Note that `tools` and `sections` are **parallel**, not one kind of sections (expanded in §4). Corresponds to `index.ts:115`.

### 3.2 Internal implementation

The core of the assembly line is `assemble()`. The steps of the real implementation (`index.ts:467-542`):

1. Merge sections from the global layer and the scope layer (`index.ts:484`; entries with the same name are shadowed by the scope layer);
2. Stable sort by `order` ascending (`index.ts:504`);
3. Evaluate dynamic text providers (text can be a function, evaluated at assemble time);
4. Evaluate tools providers, accumulating and merging each provider's schemas (`index.ts:491-503`);
5. Resolve variable providers;
6. Go through the `system-prompt/assemble` waterfall, allowing plugins to rewrite the product after assembly completes (`index.ts:532`, omitted in the teaching version).

The constructor also automatically registers two default sections (`index.ts:358-369`): the harness identity section (order `-100`, text "You are an AI agent powered by DeepSeek Harness.") and the deployment persona section (section name `deployment:persona`, order `0`). The persona section's text is passed in via the constructor's `persona` parameter (`index.ts:354`); the section name and order are fixed constants — the deployer swaps the persona by swapping the parameter, and plugins can also identify and replace it by the fixed section name.

The rendering exit `renderPrompt()` (`index.ts:212`) does only three things: interpolate `{{variable}}` per section, discard sections that render empty, and join with blank lines. `joinContextSections()` (`index.ts:236`) adds a fixed prefix to the context snapshot, declaring "this snapshot supersedes earlier runtime context" — the snapshot has **superseding** semantics, not append semantics.

### 3.3 Python reconstruction

The teaching version `ch08/system_prompt.py` fully implements the above flow (omitting step 6's waterfall and the orderTools sort configuration). The core of the registration API is `section()`:

```python
    def section(self, name, text, order: int = 0, scope=None):
        """Register one system-prompt section; corresponds to index.ts:381 section().

        Same layer + same name throws; same-name sections in the scope layer shadow the global layer at assemble time (expanded in Chapter 9).
        Registration goes through ctx.effect; calling the returned disposer unregisters (reversibility comes from Chapter 1).
        """
        layer = self._layer(scope)
        if name in layer["sections"]:
            raise ValueError(f'section "{name}" already registered (same-layer same-name duplicate)')
        entry = PromptSection(name, order, text, scope)

        def setup():
            layer["sections"][name] = entry
            self.ctx.emit("system-prompt/change", {"name": name, "op": "add"})

            def teardown():
                layer["sections"].pop(name, None)
                self.ctx.emit("system-prompt/change", {"name": name, "op": "remove"})

            return teardown

        return self.ctx.effect(setup, label=f"section:{name}")
```

Line by line: first get the scope layer (this chapter has only the global layer); same layer + same name throws directly — problem 2's "duplicate registration undetectable" becomes one loud failure here; the registration body is wrapped in `ctx.effect`, `setup` writes the section into the registry and emits a change event, and the returned `teardown` performs the exact inverse operation. The structure of `context()` is completely symmetric with `section()`, except that sections enter the contexts registry rather than sections.

The skeleton of the assembly side `assemble()`:

```python
    def assemble(self, scope=None) -> PromptAssembly:
        """Assemble one PromptAssembly; corresponds to index.ts:467 assemble().

        Steps: merge global layer and scope layer (same-name shadow) → stable sort by order
        → evaluate dynamic text → evaluate tools providers → resolve variables.
        """
        assemble_context = {"scope": scope}

        sections = self._merge("sections", scope)
        contexts = self._merge("contexts", scope)
        # Stable sort by order ascending (behavior: sort does not change the registration order of sections with the same order)
        sections.sort(key=lambda item: item.order)
        contexts.sort(key=lambda item: item.order)
```

`_merge()` implements the minimal form of the scope layer shadowing the global layer: first copy the global layer's entries, then overwrite with same-name entries from the scope layer. This chapter uses only "single-layer shadow"; the real project's scope is a chain (subagents, tasks, sessions nested layer by layer), with the full expansion in Chapter 9.

The rendering exit has the same name but a different shape from ch02's stub `render_prompt` — the stub version directly joins section text, while the full version interpolates first and then filters empty sections:

```python
def render_prompt(assembly: PromptAssembly) -> str:
    """Render the assembly product into system-prompt text; corresponds to index.ts:212 renderPrompt.

    Steps: interpolate {{variable}} per section → discard sections that render empty → join with blank lines.
    """
    parts = []
    for _name, text in assembly.sections:
        rendered = _interpolate(text, assembly.variables)
        if rendered.strip():
            parts.append(rendered)
    return "\n\n".join(parts)
```

### 3.4 Recap

Segment 2 of `main.py` demonstrates the solutions to the three problems: simulated plugins register four sections in arbitrary order (orders 100, 20, 50, 30 respectively), yet the rendering result is strictly in ascending order of order — **order is determined by order, not by registration order**; then calling `dispose_safety()` unregisters the safety section, and on re-rendering that section disappears cleanly — **registration is reversible**; at the end, `try/except` is used to register `tools:guidance` again, catching `ValueError` on the spot — **duplicates are detectable** (contrasting with the anti-pattern problem "duplicate registration undetectable").

## 4. Tool Schemas: A Parallel Independent Field

Here we need to clarify an easily confusing statement: "tool schemas are part of the system-prompt". Looking at the source code, this statement is inaccurate — `PromptAssembly` has four fields (`index.ts:115-120`), `tools` and `sections` are **two parallel components**, `renderPrompt()` consumes only `sections` (`index.ts:212-217`), and schemas never enter the rendered text.

This also answers the loose end left by Chapter 4. Stuffing the schema text into the stub section during the ch04 demo was an expedient; in the real code, `ToolRuntime` hooks `wireSchemas` to `ctx.systemPrompt.tools()` at construction time:

```typescript
// packages/core/tools/src/index.ts:832
ctx.systemPrompt.tools(context => this.wireSchemas(context.scope))
```

The teaching version copies this hookup as-is (`ch08/code/main.py` segment 3):

```python
    # Hook wireSchemas to systemPrompt.tools() — the real landing point of Chapter 4's wireSchemas
    ctx.systemPrompt.tools(lambda c: ctx.tools.wire_schemas())

    assembly = ctx.systemPrompt.assemble()
    print("assembly.tools (parallel field, passed independently to the model caller):")
    print(f"  {assembly.tools}")
```

What `tools()` registers is a **provider**, not the schemas themselves: every `assemble()` re-evaluates the providers (`index.ts:493-494`), so subsequent additions/removals of tools in the tool registry are automatically reflected in the next assembly, with no need to re-register. See §7 segment 3 for the run output: `assembly.tools` is a structured schema array, while the `render_prompt` text contains neither tool descriptions nor `parameters` — what the model caller gets is two independent input parameters: system text and tools field.

Why design it this way? Because the consumers of schemas are not only the model: callers need to do parameter validation by schema, and tool sorting and filtering by `knownNames` (`index.ts:164 orderTools`). Welding schemas into the text loses all of these structural capabilities.

## 5. Mechanism A: context() → Runtime Context Snapshot

`SystemPrompt` also has a second kind of registration: `context()`. Its API shape is completely symmetric with `section()`, but the exit is different — context sections do not enter the system text; they are rendered by `render_context_snapshot()` into one **snapshot**:

```python
def join_context_sections(sections: list) -> str:
    """Concatenate context sections and add a unified prefix; corresponds to index.ts:236 joinContextSections."""
    body = "\n\n".join(sections)
    if not body:
        return ""
    # The prefix explicitly declares "this snapshot supersedes earlier runtime context" (corresponds to the constant text at index.ts:239)
    return (
        "Current runtime context. This snapshot supersedes earlier runtime-context snapshots."
        + "\n\n"
        + body
    )
```

Who consumes the snapshot? In the real code it is agent-loop's `preStep` (`packages/core/agent-loop/src/agent.ts:225-243`): after assembling each step, render the context sections and hand them to `runtimeContext.project()` for projection; **only when the snapshot content changes** is the new snapshot appended as a user message to this step's messages (`agent.ts:238`); if the content is unchanged, no duplicate injection. The teaching version puts this glue in `ch08/prompt_agent.py`:

```python
class RuntimeContext:
    """Runtime context snapshot deduplicator; corresponds to agent.ts:233 runtimeContext.project.

    New injection text is produced only when the snapshot content changes; if the content is unchanged, returns None,
    avoiding duplicate injection of the same context at every step (the real version compares by projection identity; the teaching version compares by text).
    """

    def __init__(self):
        self._last_snapshot = None

    def project(self, snapshot_text: str):
        """Project one snapshot: return the text if changed, None if unchanged."""
        if not snapshot_text or snapshot_text == self._last_snapshot:
            return None
        self._last_snapshot = snapshot_text
        return snapshot_text
```

Segment 4 of `main.py` verifies the deduplication behavior: after registering two context sections, the first projection produces injection text, and the second projection (content unchanged) returns `None`.

Two supplementary APIs: `suppress_runtime_context()` (`index.ts:415`) temporarily forbids context registration in scenarios like compaction, during which `context()` calls throw; a context section's text can also be a dynamic provider, re-evaluated at each assemble — segment 4's `workspace:state` is one example.

## 6. Mechanism B: Three context Plugins

The three plugins under `packages/context/` provide another path: without going through `context()` registration, each injects dynamic information directly as user messages. The three have different trigger points.

### 6.1 agent-instructions: workspace instructions

Corresponds to `packages/context/agent-instructions/src/index.ts` (367 lines). When the plugin is installed, it prepares the instruction baseline (`apply` :80; the real version starts from cwd and reads files like AGENTS.md via `findProjectRoot` :125; the teaching version passes the instruction dict directly); at each step's `agent/pre-step`, it composes the baseline with the project instructions (:329), renders them into text with a `<system-reminder>` frame, and inserts them as user messages into this step's messages (`toSpliced` at :346):

The teaching version keeps the "compose → render → inject" main line. The semantics of compose is **project instructions shadow same-name blocks in the baseline** — project level is more specific and has higher priority:

```python
def compose_instructions(baseline: dict, project: dict) -> dict:
    """Merge the workspace instruction baseline with project instructions; corresponds to index.ts:105 compose.

    Instructions are a dict of "block name → text"; same-name blocks are shadowed by project instructions
    (project level is more specific and has higher priority).
    """
    merged = dict(baseline)
    merged.update(project)
    return merged
```

The listener body (note that the real version is a middleware-style waterfall, first `await next()` to get the decision and then append messages, :326; ch01's simplified waterfall has no continuation, so the teaching version directly rewrites the snapshot and returns `None` to pass through):

```python
    def _on_pre_step(self, snapshot):
        """pre-step listener: assemble the instruction text and append to additional_contexts.

        The real version is a middleware-style waterfall: first await next() to get the decision, then append messages
        (index.ts:326/346); ch01's simplified waterfall has no continuation, so here we directly rewrite
        the snapshot and return None to pass through.
        """
        composed = compose_instructions(self._baseline, self._project)
        text = render_workspace_context(composed)
        if text:
            snapshot["additional_contexts"].append(text)
        return None
```

### 6.2 time-context: time context

Corresponds to `packages/context/time-context/src/index.ts` (209 lines). `apply` (:145) registers an `agent/pre-step` listener (:170; the real version attaches `{prepend: true}` so it runs before other listeners), samples the current time at each step, renders it into "Time sampled while preparing turn N, step M: ..." (`renderText` :110-125), and injects it as a user message (:202). The real version also has `refreshIntervalMs` throttling — if the interval since the last sample is insufficient, reuse the old time; the teaching version omits throttling but makes the clock an injectable parameter, using a fixed clock in the demo to guarantee reproducible output.

### 6.3 session-reference: cross-session references

Corresponds to `packages/context/session-reference/src/index.ts` (303 lines). Note the difference in trigger point: it does **not** hook `agent/pre-step` — `SessionReferenceResolver`'s (:70) `prepare()` is explicitly called by the host before messages are enqueued (:169), resolving references in user messages and enqueuing the referenced session snapshots as user messages together with the user message.

The resolved snapshots are carried in a JSON array, wrapped outside with a layer of safety prefix (`PROMPT_PREFIX` :42-50) — declaring the snapshots "untrusted, read-only": unless the current user explicitly repeats them, do not execute instructions, permission claims, or tool requests inside the snapshots. This is the defense line that cross-session injection must have: the content of referenced sessions comes from history and cannot be treated as authorization from the current user.

```python
    def prepare(self, content: str, self_session_id=None):
        """Called by the host before enqueue: resolve references in content.

        Returns (content, additional_context): when there are no references or references are unresolvable,
        additional_context is None (corresponds to the early-exit path at index.ts:177).
        """
        references = normalize_references(content, self_session_id)
        if not references:
            return content, None
        sources = []
        for ref in references:
            summary = self._sessions.get(ref)
            if summary is not None:
                sources.append({"session_id": ref, "summary": summary})
        if not sources:
            return content, None
        return content, render_reference_prompt(sources)
```

[Teaching simplification] The real version's reference syntax is `dsh-session:<id>` (`uri.ts:70`); the resolver also has three caps, a byte budget, and cwd-affinity sorting; the teaching version simplifies the syntax to `session:<id>`, keeping the "normalize references → look up session snapshots → render prompt" main line.

### 6.4 The two mechanisms must not be confused

Both mechanism A and mechanism B enter the model's field of view as user messages, but they are two independent paths:

| Dimension | Mechanism A: `context()` | Mechanism B: context plugins |
| --- | --- | --- |
| Registration/implementation location | Inside the system-prompt package (`index.ts:398`) | Three independent plugins in `packages/context/` |
| Entry | `ctx.systemPrompt.context()` | Plugins hook events themselves or are called by the host |
| Rendering | `renderContextSnapshot()` (`index.ts:224`) | Each plugin renders itself (system-reminder frame / time text / reference prompt) |
| Injection timing | Inside the loop's `preStep`, injected only when the snapshot changes (`agent.ts:232-238`) | pre-step listeners inject at each step; session-reference injects before enqueue |
| Deduplication | `runtimeContext.project()` deduplicates by content | No built-in deduplication (the time plugin relies on throttling, omitted in the teaching version) |

While we're at it, clarify workspace's role: `WorkspaceRegistry` (:92) in `packages/workspace/workspace/src/index.ts` is an **entity registry** — `create()` (:158) registers workspace entities, `get()` (:171) retrieves by id; it does not inject any instructions. The path by which workspace instructions enter the model's field of view is: the agent-instructions plugin (§6.1's `AgentInstructionsPlugin`) reads instruction files, renders them into a `<system-reminder>` box at each step's pre-step, and injects them as user messages — it belongs to mechanism B's pre-step plugins; what the host explicitly calls before message enqueue is session-reference's `prepare()` (§6.3); don't confuse the two trigger points. Treating workspace as an "instruction injector" is a common misconception.

## 7. Full Run Output

Run `python3 ch08/code/main.py` (segments 1–4 share one container to demonstrate incremental assembly; segment 5 runs the full loop in a brand-new container; note that segment 5's new container passes a different persona `main.py:170`, incidentally demonstrating that the persona section can be replaced by the deployer), full output:

```text
== Segment 1: container and systemPrompt service — two default sections ==
Sections registered by the constructor (assemble order):
  - harness:identity: You are an AI agent powered by DeepSeek Harness.
  - deployment:persona: You are a rigorous code review assistant; answer in Chinese.

render_prompt rendering result:
You are an AI agent powered by DeepSeek Harness.

You are a rigorous code review assistant; answer in Chinese.

== Segment 2: plugins register ordered sections — assemble merges and sorts ==
Registration order: tools:guidance(order=100) → workspace:conventions(order=20)
          → safety:policy(order=50) → review:focus(order=30)
render_prompt rendering result (sections in ascending order of order):
You are an AI agent powered by DeepSeek Harness.

You are a rigorous code review assistant; answer in Chinese.

Project code is under chNN/code/; comments are in Chinese.

This review's focus: error handling and boundary conditions.

Do not output any keys or credential information.

Confirm the path exists before calling a tool.

Re-render after unregistering safety:policy (that section disappears):
You are an AI agent powered by DeepSeek Harness.

You are a rigorous code review assistant; answer in Chinese.

Project code is under chNN/code/; comments are in Chinese.

This review's focus: error handling and boundary conditions.

Confirm the path exists before calling a tool.

Duplicate registration throws: section "tools:guidance" already registered (same-layer same-name duplicate)

== Segment 3: tool schemas are a parallel independent field ==
assembly.tools (parallel field, passed independently to the model caller):
  [{'name': 'read_file', 'description': 'Read file content', 'parameters': {'path': 'file path'}}]

render_prompt text does not contain tool schemas:
  Is 'Read file content' in the system text? False
  Is 'parameters' in the system text? False

== Segment 4: mechanism A — context() → runtime context snapshot ==
render_context_snapshot rendering result (with fixed prefix):
Current runtime context. This snapshot supersedes earlier runtime-context snapshots.

Working directory: ch08/code
Git status: working tree clean

The current plan has progressed to step 3: writing the system-prompt chapter.

First projection: produces injection text
Second projection (content unchanged): None

== Segment 5: mechanism B — three plugins inject user messages inside the full loop ==
Content received by the model:
  [system prompt] (rendered by renderPrompt, does not contain tool schemas):
    You are an AI agent powered by DeepSeek Harness.
    
    You are the harness demo assistant.
  [Tool schemas] (parallel field):
    [{'name': 'read_file', 'description': 'Read file content', 'parameters': {'path': 'file path'}}]
  [Message history] (injected user messages + original user message):
    [user] Current runtime context. This snapshot supersedes earlier ru...
    [user] <system-reminder>
    [user] Time sampled while preparing turn 1, step 1: Wednesday, Augu...
    [user] ## Referenced sessions
    [user] Continue the previous work session:s-001 and check the content of sample.txt
  [Assistant reply] (session event assistant/message):
    [{'type': 'text', 'text': 'Understood: first check sample.txt, then continue the unfinished work of s-001.'}]
```

Segment 5's message history is worth checking item by item: the 1st is mechanism A's context snapshot (`RuntimeContext.project`'s first projection); the 2nd and 3rd are injections from mechanism B's two pre-step plugins (the workspace instructions' `<system-reminder>` frame, the time context); the 4th is the reference snapshot injected by session-reference before enqueue; only the 5th is the original user message. The system prompt has only two sections of text, and tool schemas are listed separately as a parallel field — one call, three channels, no mixing.

## 8. Source Mapping

| Teaching version (ch08/code/) | Real source code (/home/chenyu/deepseek-harness/) | Notes |
| --- | --- | --- |
| `system_prompt.py` `PromptSection` | `packages/core/system-prompt/src/index.ts:53` | The four elements of a section contribution |
| `system_prompt.py` `PromptContext` | Same as above `:78` | Context contribution |
| `system_prompt.py` `PromptAssembly` | Same as above `:115` | Four fields of the assembly product |
| `system_prompt.py` `PERSONA_SECTION` / `PERSONA_ORDER` | Same as above `:128` / `:131` | Persona section constants |
| `system_prompt.py` `render_prompt` | Same as above `:212` `renderPrompt` | Interpolate → filter → join |
| `system_prompt.py` `render_context_snapshot` | Same as above `:224` `renderContextSnapshot` | Context snapshot rendering |
| `system_prompt.py` `join_context_sections` | Same as above `:236` (prefix `:239`) | The snapshot prefix declares superseding semantics |
| `system_prompt.py` `SystemPrompt` | Same as above `:338` (constructor `:353`) | Default section registration `:358-369` |
| `system_prompt.py` `section` / `context` | Same as above `:381` / `:398` | Registration APIs |
| `system_prompt.py` `suppress_runtime_context` | Same as above `:415` | Suppress registration during compaction |
| `system_prompt.py` `tools` / `variable` | Same as above `:430` / `:446` | Provider-style registration |
| `system_prompt.py` `assemble` | Same as above `:467` (merge `:484`, sort `:504`) | Teaching version omits the waterfall `:532` |
| `prompt_agent.py` `RuntimeContext.project` | `packages/core/agent-loop/src/agent.ts:233` | Snapshot deduplication projection |
| `prompt_agent.py` `PromptAwareAgent.pre_step` | Same as above `:225-243` `preStep` | Assemble → project → waterfall |
| `context_plugins.py` `AgentInstructionsPlugin` | `packages/context/agent-instructions/src/index.ts:80` (pre-step `:322`) | compose `:105`, toSpliced `:346` |
| `context_plugins.py` `TimeContextPlugin` | `packages/context/time-context/src/index.ts:145` (listener `:170`) | renderText `:110-125` |
| `context_plugins.py` `SessionReferenceResolver` | `packages/context/session-reference/src/index.ts:70` (prepare `:169`) | Prefix `:42-50` |
| `main.py` `ctx.systemPrompt.tools(...)` | `packages/core/tools/src/index.ts:832` | The real landing point of wireSchemas |

Teaching simplification list (all marked [Teaching simplification] in code comments):

1. The `system-prompt/assemble` waterfall at the end of `assemble()` (`index.ts:532`) and the `orderTools` sort configuration (`index.ts:164`) are omitted;
2. scope implements only single-layer shadow; the full scope chain is expanded in Chapter 9;
3. agent-instructions does not read instructions from the file system; the instruction dict is passed in directly;
4. time-context omits `refreshIntervalMs` throttling; the clock is injectable to guarantee reproducible output;
5. session-reference's reference syntax is simplified to `session:<id>` (the real one is `dsh-session:<id>`); the three caps, byte budget, and cwd-affinity sorting are omitted;
6. In the teaching version, mechanism A's snapshot and mechanism B's injections are unified via `additional_contexts` and placed before claimed messages; in the real code, mechanism A's snapshot is appended after claimed (`agent.ts:238`);
7. `tools()` / `variable()` registrations are stored in flat containers; in the real code they are likewise written into the current scope layer via `layers.effect`, isomorphic to section/context (this chapter uses only the global layer, so behavior is consistent).

## 9. Summary and Preview

This chapter refactors "the prompt sent to the model" from a hard-coded string into a registry + assembly flow:

| Problem (§1) | This chapter's mechanism | Evidence |
| --- | --- | --- |
| Order out of control | `order` sort key, assemble's stable sort | §7 segment 2: registration order is arbitrary, output is strictly by order |
| Irreversible (including duplicate registration undetectable) | `ctx.effect` registration, disposer unregistration; same layer + same name throws | §7 segment 2: after unregistration the section disappears; `system_prompt.py` `section()` throws on same layer + same name |
| Structural collapse | tools is a parallel field of PromptAssembly | §7 segment 3: schemas are not in the render_prompt text |

The two context mechanisms each take their place: mechanism A (`context()` → snapshot projection) carries runtime context that "goes with the assembly line", and mechanism B (the three context plugins) carries dynamic information that "comes with its own rendering and trigger points". The next chapter (Chapter 9 "scope") expands the foreshadowing buried in this chapter: how the scope chain nests layer by layer, how same-name sections shadow along the chain, and why subagents can see prompts different from the main agent. The later Chapter 10 "context compaction" will use this chapter's `suppress_runtime_context()` — forbidding context registration during compaction is exactly its real use case.

## 10. Appendix: Key Concepts Cheat Sheet

Layered by dependency (lower-layer concepts depend on upper-layer concepts):

| Layer | Concept | One-line definition | First appearance |
| --- | --- | --- | --- |
| L1 kernel | `ctx.effect` | Reversible side effect: registration immediately returns an unregistration function | Chapter 1 |
| L1 kernel | `agent/pre-step` | waterfall event before each step's decision | Chapter 2 |
| L2 registration | PromptSection | Input of one section registration: name/order/text/scope | §3.1 |
| L2 registration | order | A section's sort key, unrelated to registration order | §3.1 |
| L2 registration | disposer | Return value of registration APIs; calling it unregisters | §3.1 |
| L3 assembly | PromptAssembly | assemble's product: four fields sections/contexts/tools/variables | §3.1 |
| L3 assembly | assemble() | Merge → sort → evaluate, producing PromptAssembly | §3.2 |
| L3 assembly | Scope layer shadow | Same-name entries in the scope layer overwrite the global layer (this chapter is single-layer only) | §3.3 |
| L4 rendering | renderPrompt() | sections → system text (interpolate, filter empties, join with blank lines) | §3.2 |
| L4 rendering | renderContextSnapshot() | contexts → snapshot text with superseding-semantics prefix | §5 |
| L5 injection | Mechanism A | context() sections → snapshot → project deduplication → user message | §5 |
| L5 injection | Mechanism B | context plugins each render → user message (pre-step or before enqueue) | §6 |
| L5 injection | RuntimeContext.project | Snapshot deduplicator: inject only when content changes | §5 |
