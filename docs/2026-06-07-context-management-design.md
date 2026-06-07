# Context Management for codey — design

Adds a 4-step proactive compaction pipeline plus a reactive (413) retry path
so codey can run long conversations without blowing past the model's context
window. The pipeline runs at the top of every model round inside
`Agent.run()`; cheap steps are no-ops most of the time; the LLM-summary step
only fires when the estimated token count exceeds
`context_window − max_output_tokens − compact_headroom`.

References:
- Claude Code's auto-compact + `/compact` behavior.
- codex's pre-request context trimming.
- The `agent_loop` pseudocode supplied with the brainstorm prompt.

## Goals & non-goals

**Goals**
- Persist oversized tool results to disk before they bloat the prompt; leave
  a `<persisted output>` stub the model can re-fetch via `read_file`.
- Trim middle-of-history messages when the conversation grows past 50
  non-system messages, preserving tool_call ↔ tool_result pairing.
- Replace older tool results with a short placeholder so only the latest 5
  bodies are still carried in-prompt.
- Summarize the whole conversation with one extra LLM call when the
  proactive steps aren't enough.
- Recover from the provider returning a 413 / context_length_exceeded by
  running a more aggressive compact and retrying once.
- Surface every non-trivial compaction step as a meta line in the TUI so
  the user always knows what changed.
- Let the model (a `compact` tool) or the user (a `/compact` slash command)
  trigger the summary step on demand.

**Non-goals (v1)**
- A separate "cheap summarizer model" config. The active profile/model
  summarizes its own history.
- Token-accurate counting via tiktoken or provider-side counting APIs.
  Heuristic `chars ÷ 4` is sufficient — the `compact_headroom` (default
  13000 tokens) absorbs estimation error.
- Persisting non-tool messages (user/assistant text). Only tool results
  spill to disk.
- Diff-only or selective summaries ("summarize this range"). Summary is
  whole-history each time.
- Rich UI such as a header gauge showing context utilization. Meta lines
  only.
- Restoring persisted tool results inline ("rehydrate"). Once spilled, the
  body is on disk; the model re-reads with `read_file` if it needs the full
  content.

## Module layout

A new top-level package next to `core/`, `permissions/`, `hooks/`, `tools/`,
`ui/`, `skills/`:

```
src/codey/context/
  __init__.py        # re-exports run_proactive, run_reactive, llm_compact,
                     # PromptTooLongError, MetaSink
  pipeline.py        # orchestrator: run_proactive(...) + run_reactive(...)
  budget.py          # tool_result_budget step
  snip.py            # snip_compact step (50-msg threshold, pair-aware)
  micro.py           # micro_compact step (keep last 5 tool result bodies)
  llm.py             # llm_compact_history (1 API call)
  reactive.py        # reactive_compact (on PromptTooLong, ≤1 retry/turn)
  tokens.py          # estimate(messages) — chars ÷ 4
  transcripts.py     # write_persisted_tool_result(), write_history_snapshot()
  errors.py          # PromptTooLongError + provider error sniffer
```

Each step is a **pure function over the history list**, parameterized by
thresholds and given a small `MetaSink` callable for user-visible meta
lines. They take no Agent dependency and are unit-testable in isolation,
mirroring how `core/history.py:repair` is testable today.

## Pipeline integration

`core/turn.py` calls into `context/` at the top of each round inside
`Agent.run()`:

```python
# at the top of Agent.run(), before the for-loop:
reactive_retries = 0
session_id = self._session_id        # see "Session id" below
recent_files = self._recent_reads    # see "Recent reads tracking" below

for round_idx in range(MAX_ROUNDS):
    yield RoundStarted(round=round_idx)

    # 4-step proactive pipeline. Mutates self.history in place.
    # Emits meta strings via self._meta when a step changed something.
    context_pipeline.run_proactive(
        history=self.history,
        profile=self.profile,
        session_id=session_id,
        last_round_tool_idxs=self._last_round_tool_msg_idxs,
        meta=self._meta,
        client=self._client,
        recent_files=recent_files,
    )

    try:
        text, tool_calls = "", []
        async for ev in self._stream_one_round():
            ...
    except context_errors.PromptTooLongError as e:
        if reactive_retries < REACTIVE_MAX_RETRIES:
            await context_pipeline.run_reactive(
                history=self.history,
                profile=self.profile,
                session_id=session_id,
                meta=self._meta,
                client=self._client,
                recent_files=recent_files,
            )
            reactive_retries += 1
            continue
        raise   # surfaces as TurnCompleted(reason="error") via the existing handler
    ...
```

`_stream_one_round` wraps the underlying `openai.AsyncOpenAI` call with the
provider-error sniffer from `context/errors.py` and re-raises
`PromptTooLongError` for known patterns (HTTP 413; OpenAI error code
`context_length_exceeded`; Anthropic equivalent on OpenAI-compatible
gateways: `error.type == "invalid_request_error"` with message containing
"prompt is too long" / "tokens"; DeepSeek's equivalent). The sniffer is
substring-tolerant because providers vary.

`self._last_round_tool_msg_idxs` is the list of history indices the most
recent round's tool results landed at — captured by the concurrent
dispatch in `turn.py` so `tool_result_budget` knows exactly which messages
to consider. Initialized to `[]` so the first round's pipeline call is a
no-op for the budget step.

`self._meta` is a thin shim that proxies to the meta_writer the UI passed
via `UISinks` — set on the Agent at construction time so context/ doesn't
need to import from ui/. For sub-agents the meta writer can be `None`
(see "Sub-agents" below); the pipeline checks before calling.

## Token estimator (`context/tokens.py`)

```python
def estimate(history: list[Message]) -> int:
    total_chars = 0
    for m in history:
        total_chars += len(m.content or "")
        if m.tool_calls:
            for tc in m.tool_calls:
                total_chars += len(tc.get("function", {}).get("name", ""))
                total_chars += len(tc.get("function", {}).get("arguments", ""))
        if m.name:
            total_chars += len(m.name)
    return total_chars // 4
```

Fast, dependency-free, deterministic. Good enough as a trigger because:

- The `compact_headroom` (default 13000 tokens) explicitly absorbs
  estimation error.
- The reactive path catches the case where the estimate was too low.
- Real per-model tokenizers differ by 10–20% on natural-language text
  anyway, so chasing them isn't worth the dependency footprint or the
  per-provider branching.

## Step 1 — tool_result_budget (`context/budget.py`)

**Trigger:** sum of `len(content.encode("utf-8"))` over tool messages from
the most recent round exceeds `200_000` bytes.

**Scope:** only the most-recently-completed round's tool results
(`last_round_tool_idxs`). Earlier rounds are handled by the snip/micro
steps. This matches the spec ("If in the last round of message, sum all
tool call results") and keeps the check near-free since most rounds have
zero or one tool call.

**Algorithm:**

```python
def run(
    history: list[Message],
    last_round_tool_idxs: list[int],
    session_id: str,
    meta: MetaSink | None,
) -> int:
    """Persist large tool results from the last round. Returns count persisted."""
    if not last_round_tool_idxs:
        return 0

    sizes = [(i, len(history[i].content.encode("utf-8")))
             for i in last_round_tool_idxs
             if history[i].role == "tool" and history[i].content]
    total = sum(sz for _, sz in sizes)
    if total <= TOOL_RESULT_BUDGET_BYTES:   # 200_000
        return 0

    persisted = 0
    total_bytes = 0
    for idx, sz in sizes:
        msg = history[idx]
        path = transcripts.write_persisted_tool_result(
            session_id=session_id,
            call_id=msg.tool_call_id,
            tool_name=msg.name or "tool",
            body=msg.content,
        )
        preview = msg.content[:TOOL_RESULT_PERSIST_PREVIEW_CHARS]   # 2000
        msg.content = (
            f"<persisted output>\n"
            f"path: {path}\n"
            f"original_bytes: {sz}\n"
            f"preview (first {len(preview)} of {len(msg.content)} chars):\n"
            f"{preview}"
        )
        persisted += 1
        total_bytes += sz

    if meta and persisted:
        meta(f"[ctx: persisted {persisted} tool result"
             f"{'s' if persisted > 1 else ''} "
             f"({total_bytes // 1000}kb) to disk]")
    return persisted
```

**Persisted bodies live at:**

```
~/.cache/codey/transcripts/<session_id>/tool_results/<call_id>-<tool>.txt
```

`<call_id>` is the OpenAI tool_call_id (e.g. `call_abc123`); `<tool>` is the
tool name (`bash`, `read_file`, …). `<session_id>` is the same 8-char hex
already generated by `Session.build()` for the audit log.

**Why all of them and not just the ones over 200kb individually?** The
threshold is on the *sum*, per the spec. If the round has three results of
180kb / 30kb / 10kb that sum to 220kb, persisting only the 180kb one would
still leave the prompt heavy. Persisting all three is simpler, predictable,
and the user explicitly described "tool_result1 + tool_result2 +
tool_result3 add up to 220kb > 200kb, then persist these tool call on
disk."

**Idempotence:** Persisted messages start with `<persisted output>` so a
re-run of `run` on the same round is a no-op (the size check still passes
because the bodies are now short stubs). Defensive guard: skip messages
whose content already starts with `<persisted output>`.

**Disk-write failure:** Returns the original size unchanged + emits
`[ctx: persist failed for <call_id>: <err>]` meta line and continues with
the next message. The pipeline never raises; the model just sees the full
tool result and the downstream steps still run.

## Step 2 — snip_compact (`context/snip.py`)

**Trigger:** `len(non_system_messages) > 50`.

**Algorithm:** Keep the **first 5** non-system messages (anchoring the
conversation's initial framing) and the **last 45** non-system messages
(carrying the live context). Drop everything in between. Replace the
dropped block with one synthetic gap marker message:

```python
Message(role="user", content="[... N earlier messages compacted by snip ...]")
```

**Pair preservation:** If the cut boundary on either side falls inside a
tool_call ↔ tool_result group, push the cut outward so no orphans survive.
Specifically:

- **Left side (end of the kept prefix):** if message at index 4 has
  `tool_calls`, the keep-prefix needs to be extended to include every
  matching tool result. Walk forward from index 4 absorbing role:"tool"
  messages with matching `tool_call_id` until all expected ids are
  satisfied; the new prefix-end is one past the last tool message.

- **Right side (start of the kept suffix):** if the first message of the
  kept suffix is role:"tool", walk backward absorbing all role:"tool"
  messages that share its call group and the preceding `assistant.tool_calls`
  message. The new suffix-start is the index of that assistant message.

- **Edge:** if the two windows ever overlap after expansion, do nothing
  (snip would be a no-op).

Indices are relative to non-system messages. System messages stay at the
front of `history` and aren't counted in the 50 / 5 / 45.

```python
def run(history: list[Message], meta: MetaSink | None) -> int:
    sys_count = sum(1 for m in history if m.role == "system")
    body = history[sys_count:]
    if len(body) <= SNIP_THRESHOLD_MESSAGES:    # 50
        return 0

    prefix_end = _expand_prefix_to_pair_boundary(body, SNIP_KEEP_HEAD)  # 5
    suffix_start = _expand_suffix_to_pair_boundary(
        body, len(body) - SNIP_KEEP_TAIL                                 # 45
    )
    if suffix_start <= prefix_end:
        return 0    # the two windows overlap; nothing to snip

    dropped = suffix_start - prefix_end
    if dropped <= 0:
        return 0

    marker = Message(
        role="user",
        content=f"[... {dropped} earlier message{'s' if dropped > 1 else ''} "
                f"compacted by snip ...]",
    )
    new_body = body[:prefix_end] + [marker] + body[suffix_start:]
    history[sys_count:] = new_body

    if meta:
        meta(f"[ctx: snipped {dropped} middle messages]")
    return dropped
```

**Why a marker?** A silent delete makes the model's next reply feel
discontinuous when an earlier topic suddenly vanishes. A one-line marker
costs ~15 tokens and explicitly tells the model "there's a gap here."

**Why expand outward instead of shrinking?** Shrinking the window can drop
the most relevant pair (the boundary is by definition the most recent
prefix info / earliest tail info). Expanding outward never loses pairs and
the cost is at most a handful of extra messages per cut.

**Helper contract** (used by snip and reused by reactive):

- `_expand_prefix_to_pair_boundary(body, end_idx) -> int` — given a
  proposed prefix end, returns a possibly-larger index that includes any
  trailing assistant.tool_calls' matching role:"tool" messages. If
  `body[end_idx-1]` has `tool_calls`, walk forward absorbing role:"tool"
  messages whose `tool_call_id` is in the expected set, until all
  expected ids are covered. Returns the new exclusive end index.

- `_expand_suffix_to_pair_boundary(body, start_idx) -> int` — given a
  proposed suffix start, returns a possibly-smaller index that pulls in
  the originating assistant.tool_calls if `body[start_idx]` is a
  role:"tool" message. Walk backward over consecutive role:"tool"
  messages, then check the preceding assistant message: if it has
  `tool_calls` and any of those `id`s match the absorbed tool messages,
  the new start index is the index of that assistant message.

Both helpers are pure functions over `body` (a list of Message); both
live in `snip.py` and are re-exported for reactive's use. Edge case: if
`end_idx` or `start_idx` is at the boundary of `body`, the function
returns the input unchanged.

## Step 3 — micro_compact (`context/micro.py`)

**Trigger:** always runs (no threshold). Walks history and replaces all
but the **last 5** tool-result messages' bodies with a fixed placeholder.

**Algorithm:**

```python
PLACEHOLDER = "[Earlier tool result compacted. Re-run if needed.]"

def run(history: list[Message], meta: MetaSink | None) -> int:
    tool_idxs = [i for i, m in enumerate(history) if m.role == "tool"]
    if len(tool_idxs) <= MICRO_KEEP_RECENT_TOOL_RESULTS:   # 5
        return 0
    cutoff = len(tool_idxs) - MICRO_KEEP_RECENT_TOOL_RESULTS
    replaced = 0
    for i in tool_idxs[:cutoff]:
        if history[i].content != PLACEHOLDER:
            history[i].content = PLACEHOLDER
            replaced += 1
    if meta and replaced:
        meta(f"[ctx: replaced {replaced} old tool result"
             f"{'s' if replaced > 1 else ''} with placeholder]")
    return replaced
```

**Why no threshold?** The cost of the walk is negligible (one pass + at
most N string assignments), and the operation is idempotent — re-running
it does nothing once everything is placeholder-ized. Putting a trigger
threshold just adds dead config. The step is silent when no message
actually changed (the `if meta and replaced` guard suppresses the meta
line on the second and subsequent runs).

**Why this placeholder text?** It's short (~9 tokens) and tells the model
exactly what to do if it needs the data again ("Re-run if needed"). Same
phrasing the user supplied.

**Important interaction with the budget step:** A persisted tool result
(`<persisted output>` stub from step 1) gets rewritten to the generic
placeholder by step 3 once it's older than the last 5. This is intentional
— after the 5-tool-result window passes, the model rarely needs the
preview either, and the full body is still on disk. If the model needs it
back, the `<persisted output>` line in the conversation context (still
visible inside the most-recent 5) or the audit log point at the file path.

## Step 4 — llm_compact_history (`context/llm.py`)

**Trigger:** `tokens.estimate(history) > profile.context_window −
profile.max_output_tokens − profile.compact_headroom`. After steps 1–3
ran. Cheapest is to evaluate once after step 3 inside `run_proactive`.

**Algorithm:**

```python
async def run(
    history: list[Message],
    profile: Profile,
    session_id: str,
    meta: MetaSink | None,
    client: AsyncOpenAI,
    recent_files: list[Path],
) -> bool:
    # 1) Snapshot the pre-compact history to disk.
    snapshot_path = transcripts.write_history_snapshot(
        session_id=session_id, history=history, kind="proactive"
    )

    # 2) Ask the model to summarize.
    summary = await _summarize(client, profile, history)

    # 3) Re-read up to 5 recently-read files (fresh content as of NOW).
    file_blocks = _read_recent_files(recent_files, max_files=5)

    # 4) Replace history with: [system messages...] +
    #    one synthetic user message holding the summary + the file blocks.
    system_msgs = [m for m in history if m.role == "system"]
    body = _build_replacement_user_message(summary, file_blocks, snapshot_path)
    history[:] = system_msgs + [Message(role="user", content=body)]

    if meta:
        meta(f"[ctx: summarized history → 1 message "
             f"(snapshot: {snapshot_path})]")
    return True
```

The summarize call uses the **same client + profile** as the active agent.
This keeps config simple (no second profile to manage) and means a
high-quality model summarizes itself; the cost is one extra request per
compaction trigger, which should be rare.

**Summary prompt** (sent as a one-shot OpenAI chat-completions call —
streaming off, no tools, low temperature):

```
System: You are a context-compaction assistant. Given the conversation
history below, produce a concise summary suitable for resuming the
conversation. Cover:
1. What the user is trying to accomplish (the overarching goal).
2. Key decisions made, constraints identified, and architecture choices.
3. Files / paths / functions touched or referenced, with one-line
   purpose each.
4. The current state of work — what's done, what's in progress, what's
   blocked, and on what.
5. Anything the user explicitly asked the assistant to remember or avoid.

Be specific. Preserve names, paths, and numbers. Do not invent details
that aren't in the history. Output plain prose; no headings, no JSON.
Aim for 400–800 words.

User: [the entire pre-compact history rendered as a transcript]
```

The transcript-rendering helper formats each history message as
`role: content` lines with tool_calls flattened to one line each. Anything
already a `<persisted output>` stub or the micro placeholder gets a single
line: "(earlier tool output omitted)".

**Replacement message shape:**

```
[Conversation compacted at <ISO timestamp>. Snapshot: <snapshot_path>.]

Summary of prior conversation:
<summary text from the model>

Recent files (re-read at compact time):

--- <path1> ---
<file contents>

--- <path2> ---
<file contents>

(... up to 5 files ...)
```

If a recent-file read fails (file deleted, permission, etc.), include
`--- <path> ---\n(error: <reason>)\n` instead and continue.

**Recent reads tracking:** Maintain a small deque on the Agent
(`self._recent_reads: deque[Path]`, maxlen=5). Add one new built-in hook
`hooks/builtin/recent_reads.py` that listens on PostToolUse and pushes
the resolved `path` argument onto the deque when `tool == "read_file"`
and `ok is True`. (Keeping it as its own hook rather than overloading
`transcript.py` matches the project's one-file-per-hook style and is
trivial to disable for tests that don't want it.) Dedupe by path
(re-read moves the path to the end). Children initialize their own
empty deque.

**Why same model not a cheaper one?** A single config switch ("use
cheaper model X for compaction") encourages a knob nobody tunes. We can
add it later if cost becomes a real concern; until then, "the model you
chose is the model that summarizes" is the predictable rule.

**Why up to 5 files and re-read at compact time?** They're typically the
files the conversation is "about" right now. Re-reading guarantees the
post-compact context reflects the **current** file state, not whatever
the model saw when it first opened the file — relevant when there's been
editing during the session.

## Step 5 (failure path) — reactive_compact (`context/reactive.py`)

**Trigger:** `_stream_one_round` raised `PromptTooLongError` (sniffed from
provider error). The proactive pipeline either underestimated or didn't
fire (a single huge persisted preview can push estimates wrong; rare).

**Algorithm:**

```python
async def run(
    history: list[Message],
    profile: Profile,
    session_id: str,
    meta: MetaSink | None,
    client: AsyncOpenAI,
    recent_files: list[Path],
) -> None:
    # 1) Snapshot.
    snapshot_path = transcripts.write_history_snapshot(
        session_id=session_id, history=history, kind="reactive"
    )
    # 2) Summarize same way llm_compact does.
    summary = await llm._summarize(client, profile, history)
    file_blocks = llm._read_recent_files(recent_files, max_files=5)

    # 3) Pick a tail-start: keep at most last REACTIVE_TAIL messages,
    #    expanded outward for tool pair safety (same helpers as snip).
    sys_count = sum(1 for m in history if m.role == "system")
    body = history[sys_count:]
    tail_start_in_body = max(0, len(body) - REACTIVE_TAIL)    # 5
    tail_start_in_body = snip._expand_suffix_to_pair_boundary(
        body, tail_start_in_body
    )
    tail = body[tail_start_in_body:]

    # 4) Replace.
    system_msgs = history[:sys_count]
    intro = Message(
        role="user",
        content=llm._build_replacement_user_message(
            summary, file_blocks, snapshot_path,
            header="[Reactive compact triggered after PromptTooLong]",
        ),
    )
    history[:] = system_msgs + [intro] + tail

    if meta:
        meta(f"[ctx: reactive compact triggered (kept last {len(tail)} msgs)]")
```

**Retry policy:** `REACTIVE_MAX_RETRIES = 1` per turn. On the second
PromptTooLongError, re-raise; the existing `except BaseException` in
`Agent.run()` rolls history back to baseline and surfaces
`TurnCompleted(reason="error", error="PromptTooLongError: ...")` to the
UI. The user can `/reset` or `/compact` and try again.

**Why limit to 1 retry?** If reactive_compact runs and the next request
*still* fails, something pathological is happening (the file blocks are
themselves over context, or the model just refused to summarize). Looping
costs money and time; surfacing the error lets the user act.

## Model tool — `compact`

Add `src/codey/tools/compact.py`:

```python
@dataclass
class CompactTool:
    name: str = "compact"
    description: str = (
        "Force compact the conversation history NOW. Summarizes prior turns "
        "into a short message and re-injects the most recent files you read. "
        "After this returns, the current turn ends; you'll see the compacted "
        "context on the next user message. Call this when the conversation "
        "is long and you want a clean slate while preserving what you've "
        "learned."
    )
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    })
    session_provider: Callable[[], "Session"] = None   # injected at register time

    async def run(self, arguments: dict[str, Any]) -> str:
        sess = self.session_provider()
        await context_pipeline.run_proactive_force_summary(
            history=sess.agent.history,
            profile=sess.agent.profile,
            session_id=sess.session_id,
            meta=sess.agent._meta,
            client=sess.agent._client,
            recent_files=sess.agent._recent_reads,
        )
        return "[Compacted. History summarized.]"
```

`run_proactive_force_summary` is a thin variant of `run_proactive` that
always runs the LLM-summary step (skipping the threshold check) after
running steps 1–3.

**Turn termination:** Per the pseudocode (`break  # end the current turn,
start fresh next round with the compacted context`), the agent loop
should treat a successful `compact` tool result as a signal to break out
of the round loop. Two ways to implement:

1. Add a return signal — the tool returns the string AND somehow flags
   the loop to break.
2. Have the orchestrator notice that the model's only tool call was
   `compact` and stop the round loop after dispatching it.

Choosing **option 2** because it keeps the tool-result-is-just-a-string
invariant. The dispatch loop in `turn.py` adds, **after the
`per_call_events` are yielded** (so the ToolResult event reaches the
UI / hooks) and **before the next iteration's `RoundStarted`**:

```python
if len(tool_calls) == 1 and tool_calls[0]["function"]["name"] == "compact":
    # The compact tool ran; end the turn so the next user prompt sees
    # the compacted history. AssistantMessageCompleted still fires below
    # (the assistant text — if any — was already accumulated in
    # assistant_text_parts).
    break
```

The `AssistantMessageCompleted` + `TurnCompleted(reason="stop")` yields
that follow the for-loop fire normally; the user just sees the turn end
right after the compact meta line. Sub-agents do **not** get `compact`
(added to `EXCLUDED_FROM_CHILD` in `Session.build_child_agent`); they
inherit the auto-pipeline instead.

## User slash command — `/compact`

Add to `ui/slash_commands.py:_build_slash_commands`:

```python
SlashCommand(
    name="/compact",
    description="Summarize history into a single message; preserves system "
                "prompts and re-reads recent files.",
    handler=lambda app, _: app.session.agent.compact_now(),
),
```

Where `Agent.compact_now()` is:

```python
async def compact_now(self) -> None:
    await context_pipeline.run_proactive_force_summary(
        history=self.history, profile=self.profile,
        session_id=self._session_id, meta=self._meta,
        client=self._client, recent_files=self._recent_reads,
    )
```

Available regardless of context size — useful when the user knows
they're about to ask something big and wants a clean slate while
preserving the conversation's accumulated context.

## Session id wiring

The 8-char hex session id already lives on `Session` (used by the audit
log). It needs to reach `context/` (for transcript paths) and the Agent
(for the `compact` slash command). Two small additions:

- `Agent.__init__` gains `session_id: str = ""`. Default empty so existing
  tests that construct an Agent directly don't break.
- `Session.build()` passes `session_id=session_id` when constructing the
  Agent. Children get their own `child_session_id` (already computed in
  `build_child_agent`).
- `Agent` also gains `_meta` (the meta_writer the UI passed) and
  `_recent_reads: deque[Path]`. Both set by `Session.build()` after Agent
  construction.

These are additive — no existing call sites change behavior.

## Profile config additions

Three new optional fields on each `[profiles.*]` entry in
`~/.config/codey/config.toml`:

```toml
[profiles.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key  = "..."
model    = "deepseek-chat"
context_window     = 128000   # default if absent
max_output_tokens  = 4096     # default if absent
compact_headroom   = 13000    # default if absent
```

`Profile` (the frozen dataclass in `src/codey/config.py`) gains three
fields with the defaults above. `ConfigFile.load()` reads the optional
keys if present; absent keys use the dataclass defaults. No backwards
compatibility break — existing config.toml files continue to load.

These defaults work for most of today's mainstream long-context models
(GPT-4o, Claude 3.5 Sonnet, DeepSeek-Chat, Qwen-2.5 etc.). Users on
smaller-context models override per profile.

## Sub-agents

Children get the same pipeline but with slightly lower thresholds, since
their context budget is typically shorter-lived (one investigation, return
result, terminate):

| Threshold | Parent | Child |
|---|---|---|
| `TOOL_RESULT_BUDGET_BYTES` | 200_000 | 200_000 (same) |
| `SNIP_THRESHOLD_MESSAGES`  | 50      | 30 |
| `SNIP_KEEP_HEAD`           | 5       | 3 |
| `SNIP_KEEP_TAIL`           | 45      | 25 |
| `MICRO_KEEP_RECENT_TOOL_RESULTS` | 5 | 5 (same) |
| `compact_headroom`         | 13_000  | 13_000 (same) |

Child constants live alongside parent constants in
`context/pipeline.py`; `run_proactive` accepts a `thresholds` parameter
defaulting to `PARENT_THRESHOLDS`, with `CHILD_THRESHOLDS` selected by
`Session.build_child_agent` when it constructs the child Agent.

Children inherit profile config (so `context_window` etc. flow through
unchanged) and they do **not** get the `compact` tool (`EXCLUDED_FROM_CHILD`
gets `"compact"` added alongside `"spawn_agent"` and `"todo_write"`). They
inherit the same `recent_reads` deque type but start with an empty one,
and they inherit the parent's meta_writer for now (so child compaction
events surface on the parent transcript — matches how `subagent_render`
already injects `⏵ / ⏷` lines). If this gets noisy in practice we can
gate it on a flag later.

## UI surface — meta lines

Every non-trivial step emits one meta line via the existing `meta_writer`
(same channel used by `[turn finished: ...]`, `↳ skill loaded: ...`,
`⏵ / ⏷` sub-agent markers). All silent on no-op.

Format and examples:

| Step | Meta line |
|---|---|
| budget (≥1 persisted) | `[ctx: persisted 3 tool results (220kb) to disk]` |
| budget (write error)  | `[ctx: persist failed for call_abc: PermissionError ...]` |
| snip                  | `[ctx: snipped 18 middle messages]` |
| micro                 | `[ctx: replaced 12 old tool results with placeholder]` |
| llm_compact           | `[ctx: summarized history → 1 message (snapshot: ~/.cache/codey/transcripts/abc123/snapshots/2026-06-07T14:32:11-proactive.json)]` |
| reactive              | `[ctx: reactive compact triggered (kept last 6 msgs)]` |
| /compact or `compact` tool | `[ctx: forced compaction by /compact]` or `[ctx: forced compaction by model]` |

Lines are exactly long enough to be greppable in the transcript and short
enough not to dominate the visual flow. They appear interleaved with the
existing `[turn finished: ...]` / `↳` markers, which is the convention
the user has already accepted in the project.

## Disk layout (`context/transcripts.py`)

```
~/.cache/codey/
  calls.jsonl                           # existing audit log
  transcripts/
    <session_id>/
      tool_results/
        <call_id>-<tool>.txt            # one per persisted tool result
      snapshots/
        <ISO-timestamp>-proactive.json  # llm_compact snapshot
        <ISO-timestamp>-reactive.json   # reactive_compact snapshot
```

Snapshot files are JSON arrays of `Message.to_wire()` dicts —
self-contained replays. Useful for debugging "what was in the model's
prompt right before compaction." Snapshot writes are best-effort: if the
write fails the meta line says `(snapshot: write failed: <err>)` and
compaction proceeds.

**Permissions:** files written with `0o600` (mode set after write,
swallowing OSError on filesystems that don't honor it). Directories
created with `parents=True, exist_ok=True`.

**Cleanup:** no automatic cleanup in v1. Documented in
README under "Where codey writes." Manual: `rm -rf ~/.cache/codey/transcripts/<session_id>`
once a session is done. Easy follow-up: an audit-log-style 30-day reaper.

## Wiring summary (file-by-file)

| File | Change |
|---|---|
| `src/codey/context/` | **NEW** package, 10 files (see Module layout). |
| `src/codey/config.py` | `Profile` gains `context_window: int = 128000`, `max_output_tokens: int = 4096`, `compact_headroom: int = 13000`. `ConfigFile.load()` reads optional keys. |
| `src/codey/core/turn.py` | Import context pipeline. In `Agent.run()`: track `last_round_tool_msg_idxs`, call `run_proactive` at top of each round, catch `PromptTooLongError` and call `run_reactive` (≤1 retry). Break the round loop when only-tool-call is `compact`. |
| `src/codey/core/turn.py` `Agent` | New fields: `session_id: str = ""`, `_meta: Callable | None = None`, `_recent_reads: deque[Path]`. New method `compact_now()`. |
| `src/codey/core/streaming.py` | Wrap the provider call in a try/except that maps known errors to `context.errors.PromptTooLongError`. |
| `src/codey/core/session.py` | `Session.build()` sets `agent.session_id = session_id`, `agent._meta = ui_sinks.meta_writer`, `agent._recent_reads = deque(maxlen=5)`. Register `CompactTool(session_provider=lambda: sess)`. `build_child_agent` does the same for the child (own deque, parent's meta_writer, no compact tool, child thresholds). |
| `src/codey/hooks/builtin/__init__.py` | Add a tiny `recent_reads` hook factory (or extend `transcript.py`) that pushes successful `read_file` paths onto `agent._recent_reads`. |
| `src/codey/tools/compact.py` | **NEW** CompactTool (see Model tool section). |
| `src/codey/tools/__init__.py` | `build_default_registry` does NOT register CompactTool (needs the session). Registered from `Session.build()` like `SpawnAgentTool`. |
| `src/codey/ui/slash_commands.py` | Add `/compact` command. |
| `tests/` | New: `test_context_budget.py`, `test_context_snip.py`, `test_context_micro.py`, `test_context_llm.py` (with a stub client), `test_context_reactive.py`, `test_context_pipeline.py` (the orchestrator), `test_context_tools.py` (CompactTool + slash). |

The whole package (excluding tests) should fit in ≤700 LOC.

## History invariants and ordering

Three invariants the pipeline must preserve, in addition to those already
documented in CLAUDE.md (every assistant.tool_calls followed by matching
role:"tool" messages):

1. **System messages stay at the front.** Every step's algorithm starts
   by computing `sys_count` and operates on the slice `history[sys_count:]`.
   System messages are never dropped, summarized, or persisted.

2. **Tool pair integrity.** snip's boundary-expansion logic + reactive's
   reuse of `snip._expand_suffix_to_pair_boundary` guarantee no orphan
   `tool_calls` or orphan `role:"tool"` survive a compaction. After
   compaction `core/history.py:repair` is a defensive net but should
   find nothing to fix.

3. **Idempotence.** Running the proactive pipeline twice in a row is a
   no-op (steps 1–3 detect already-persisted / already-placeholderized /
   not-over-50-msg state; step 4 only fires if estimate is still over
   threshold, which it won't be immediately after a summarize). This is
   important because the pipeline runs at the top of every round.

## Concurrency note

`Agent.run()` already dispatches tool calls concurrently via
`asyncio.gather`. The pipeline runs **between rounds**, sequentially with
the rest of `run()`. No new concurrency. The history is mutated in place
under the same await-points the existing loop uses, so the existing rule
("nondeterministic tool-result append order is wire-safe because the API
matches by tool_call_id") is unchanged.

The recent-reads deque is only written by hooks (sequentially per call,
because hooks fire from inside `_dispatch_one`). No locking needed.

## Tests

| File | What it asserts |
|---|---|
| `test_context_budget.py` | Below-threshold round → no-op. Above-threshold sum → files written under `~/.cache/codey/transcripts/<sid>/tool_results/`, stub contains preview, idempotent on re-run. Disk-write failure path yields meta + leaves message unchanged. |
| `test_context_snip.py` | <50 msgs → no-op. >50 msgs without boundary pair → 5+45 kept + gap marker. Boundary inside a pair on left → prefix extends to cover all matching tool results. Boundary inside a pair on right → suffix extends to include the assistant.tool_calls. Overlap edge case → no-op. |
| `test_context_micro.py` | ≤5 tool messages → no-op. >5 → all but last 5 get the placeholder; second run is no-op. |
| `test_context_llm.py` | Stub OpenAI client returns canned summary. Verifies: system messages preserved, history becomes [system...] + [synthesized user msg], snapshot file written, recent files re-read (with missing-file fallback). Estimate-threshold trigger logic. |
| `test_context_reactive.py` | Stub `_stream_one_round` raises PromptTooLongError. First raise → reactive runs + retry succeeds. Second raise in same turn → re-raises (history rolled back per existing handler). |
| `test_context_pipeline.py` | End-to-end ordering: budget runs before snip, snip before micro, micro before llm; meta lines fire in order; sub-agent thresholds override parent thresholds correctly. |
| `test_context_tools.py` | `compact` tool returns the literal `[Compacted. History summarized.]` and the dispatch loop breaks out of further rounds. `/compact` slash command calls `agent.compact_now()`. |
| `tests/test_config.py` (existing or new) | Profile loads with all 3 new fields; loads with none (defaults applied); loads with partial set. |

Total: ~15–20 new tests. Existing tests should not need changes — the
pipeline's `run_proactive` is a no-op on the small histories the tests use
(tool_result_budget sees <200kb, snip sees <50 msgs, micro sees ≤5 tools,
llm under threshold).

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Provider error sniffer misses a new variant of PromptTooLong | medium | Centralize the substring patterns in `context/errors.py`, document the format, log unrecognized 4xx/5xx with their error body so we can add patterns later. Worst case: user sees `TurnCompleted(reason="error")` like today. |
| llm_compact's summary loses critical context the model needs next round | medium | The summary prompt explicitly asks for goals, decisions, file paths, current state. The last 5 recently-read files are re-injected fresh. Snapshot on disk means a future `/replay` or manual recovery is always possible. If a user reports a specific loss, tune the prompt. |
| Persisted file paths leak through logs / OTel attributes | low | The persisted path is content inside a tool result message, not a span attribute. Audit log already logs full tool results in PR-C, so this changes nothing about exposure. |
| Compaction cost adds noticeable latency on every long turn | low | Steps 1–3 are O(N) over the history list (microseconds). Step 4 is one API call but only fires past the headroom-adjusted threshold — typically once per long conversation, not per round. |
| Sub-agent's parent meta_writer prints "[ctx: ...]" lines that confuse the user about which agent is compacting | low | Acceptable for v1; matches how `subagent_render` already announces `⏵/⏷`. If noisy in practice, prefix with `[sub-agent[N]: ctx: ...]`. |
| The 13000-token compact_headroom is wrong for tiny-context models (4k, 8k) | low | Users configuring a small-context profile will quickly see proactive compact never firing (and reactive instead), which is a signal to set `compact_headroom` smaller. Document this in README. |
| `compact` tool called by model in a multi-tool-call round (alongside e.g. `bash`) leaves history in a weird state | medium | The break-on-only-compact check requires the round to contain *only* a `compact` call. If `compact` is mixed with other calls, all of them dispatch normally, history isn't terminated, and the model sees compacted history on the next round. Add a sentence to the `compact` tool description asking the model to call it alone. |

## Out of scope (deferred)

- A second `compact_profile` config field to use a cheaper model for the
  summary.
- Token-accurate counting via tiktoken / provider count endpoints.
- Automatic cleanup of old transcript dirs (write a separate small reaper
  PR if it ever matters).
- A header gauge in the TUI showing context utilization.
- `/replay <snapshot>` slash command to restore a snapshot. Useful but
  separate; the snapshots are written so this can be added later.
- Persisting non-tool messages (user/assistant text). The summary covers
  them.
- Surfacing the persisted-file path back into the prompt as a clickable
  reference. The stub already includes the path; clickability is a UI
  concern.

## Migration sequence

Ships as **one PR** (call it PR-D following the refactor-plan PR
naming). Three commits, each leaving the tests green:

1. **Add `context/` package + token estimator + transcripts I/O + the
   three pure proactive steps (budget, snip, micro) + their tests.** No
   wiring into `turn.py` yet. Pure functions only; no LLM calls. **Gate:**
   ~210 tests pass (the new ones plus the existing).

2. **Add `llm.py` + `reactive.py` + `pipeline.py` orchestrator + tests
   with a stub OpenAI client.** Profile gains the 3 new fields. Still no
   wiring into `turn.py`. **Gate:** ~220 tests pass.

3. **Wire `context_pipeline.run_proactive`/`run_reactive` into
   `Agent.run()`. Wire `_session_id`, `_meta`, `_recent_reads` onto Agent
   from Session.build. Add `CompactTool` + register from Session.build.
   Add `/compact` slash command. Update CLAUDE.md "Where things go"
   table.** **Gate:** ~225 tests pass; manual smoke test in TUI:
   - long conversation reaches snip threshold → meta line + history visibly shorter on next turn,
   - call `bash 'cat huge.log'` → budget meta line + file appears under
     `~/.cache/codey/transcripts/<sid>/tool_results/`,
   - run `/compact` → meta line + next turn proceeds with summarized context.

Total: ~3 commits.
