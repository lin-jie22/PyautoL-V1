# Can an agent make itself better? A 172-round negative result, with a measurement layer that survives it

**V1 Technical Report — 2026-11-14**

---

## Abstract

We report 172 rounds of experiments on a question we could not answer affirmatively:
*can an LLM agent, given memory, verification, and a reward signal, improve itself
without a human writing the knowledge it improves with?*

The short answer is **no**, and the reason is structural rather than a matter of
tuning. We spent 54 rounds (V19–V73) on payload, width, scope, and prompt wording —
all null or negative. We then spent 18 rounds (V74–V91) asking a different question,
*where does knowledge fail to arrive*, and located four deterministic gates in the
retrieval path. Those gates are real and reproducible. They also explain nothing about
how the system could **produce** knowledge.

We report three artifacts, in decreasing order of confidence:

1. **A measurement-validity layer** that makes every "it got better" claim falsifiable.
   Noise floor of zero (four independent confirmations), placebo arms, and 212
   withdrawal cases each proven non-vacuous by deleting the implementation.
2. **A capability ladder** with an honest position: L0–L2 complete, L3 half, L4 and L5 absent.
3. **A negative result with a mechanism**: self-evaluation by a judge drawn from the
   same distribution as the candidate has an information-theoretic ceiling of zero.
   We measured this ceiling twice, from two directions, and got 0/8 and 4/4-`NONE`.

Everything here is reproducible offline except the LLM calls, which use a local
`qwen2.5:7b` at temperature 0.

---

## 1. The system under test

V1 is a ~12.5k-line pure-stdlib agent kernel (23 modules, zero third-party runtime
dependencies, enforced by AST audit). It exposes a seven-stage cognitive loop, a
budget ledger, a runtime guard, a three-tier memory, and a verification layer that
derives all criteria from the task statement.

The evaluation surface is **67 tasks** across 17 suites. This is important and
limiting: every task asks for **one pure function**. There is no multi-file editing,
no state, no long-horizon planning, no external action. This is unit-test scale.
Any claim about "general intelligence" made from scores on this surface is void.

| Suite | n | Best mean |
|---|---|---|
| `coding_small` | 6 | 0.9722 (saturated) |
| `numeric_logic_heldout` | 3 | 1.0000 |
| `g7j_n4_heldout` (B arm) | 4 | 0.8500 |
| `struct_convert_heldout` | 4 | 0.7500 |
| `g7j_n4_heldout` (A arm) | 4 | 0.2000 |

---

## 2. What we can defend: the measurement layer

Most agent projects report a score. We report a score plus the machinery to doubt it.

**Noise floor = 0.** At T=0, repeated runs are byte-identical on the same seed. We
confirmed this four separate times (V85, V88, V89, V91) across different arms.
This is not a claim that the *system* is deterministic — it is a claim that the
*measurement* is, which is what makes deltas of 0.2 interpretable at all.

**Placebo arms.** Every knowledge-injection experiment carries a control arm with
equal-length, non-empty, non-original text. In V85 the placebo scored 0.30 while
real knowledge scored 0.85 and the empty library scored 0.20 — three arms differing
only in the starting library.

**Withdrawal cases (212).** Each assertion in the contract suite is paired with a
proof that deleting the implementation turns it red. An assertion that stays green
under deletion is vacuous, and we count how many we have (zero in the current suite).

**Shape noise = 0.1290.** We measured how much the score moves when the *form* of an
answer changes but not its content. Any claimed effect below this is not an effect.

Without this layer, the rest of this report would be unreadable, because most of our
effects are 0.2–0.8 and most of our failures look like successes.

---

## 3. The four gates (V74–V91)

We stopped asking "is memory useful" and asked "where does it fail to arrive".
Four deterministic gates, each with a boundary verified positionally:

### 3.1 Gate 1 — a 24-token query window

`fts_query()` builds `" OR ".join(f'"{t}"' for t in toks[:24])`. **Token 25 never
enters the query.** Verified by construction, not by inspection: 23 filler tokens
plus a keyword at position 24 retrieves; 24 filler tokens plus the same keyword at
position 25 retrieves nothing.

Consequence: the entire `g7j` suite retrieves because its `ZK1..ZK4` labels land at
positions 9–16. This is an artifact of how the task statements were written, not
evidence of retrieval capability.

### 3.2 Gate 2 — tokenization

`_TOKEN` treats a run of CJK characters as **one** token. Chinese query hit rate is
**1/8**. This is pinned by contract AZ26 (`[1,1,1,1]` / `[4,4,4,4]` / `[0,0,1,1]`)
and cannot be changed without changing the anchor layer.

### 3.3 Gate 3 — position is decided by the fact's own token count

`bm25` scores are **constants** across four different task statements — because the
task statement literally lists all four labels, so all four facts always match and
semantic content contributes nothing. The ordering is by token count:

| fact | chars | tokens | bm25 |
|---|---|---|---|
| ZK1 | 43 | 4 | −1.056412 |
| ZK2 | 40 | 6 | −0.933533 |
| ZK4 | 56 | 9 | −0.794852 |
| ZK3 | 63 | 12 | −0.692044 |

ZK2 is *three characters shorter* than ZK1 yet ranks second, because it has more tokens.
This is the write side's only demonstrated causal lever: **shorter ranks higher**.

### 3.4 Gate 4 — position is neither sufficient nor necessary (V91)

This is the gate that killed our own hypothesis. Three arms, 20 cells:

- **L2** (all four facts padded equally, so **order is byte-identical to baseline**)
  dropped `g7e1` from 1.0 to 0.4. → position is **not sufficient**.
- **L1** (ZK1 padded, so the correct fact for `g7d2` falls from rank 1 to rank 2)
  left `g7d2` at 1.0 (5/5). → position is **not necessary**.

Per-task sensitivity turned out to be disjoint:

| task | sensitive to |
|---|---|
| `g7d2` | nothing we varied |
| `g7d3` | ZK2's rank (+0.2, never reaches 1.0) |
| `g7e1` | **ZK1's length** (−0.6) |
| `g7e2` | **ZK1's rank** (−0.8) |

Both padding arms lowered the mean (0.85 → 0.55 and 0.70). Writing *more* is not
a strategy.

---

## 4. The negative result: self-evaluation has a ceiling of zero

Two experiments, two directions, same wall.

### 4.1 The system cannot author its own criteria (V78–V81)

Asked to produce assertions that would catch its own errors, the system produced
assertions with **discriminative power 0/8** — they were green on both correct and
incorrect solutions. The assertions are copies of the same misunderstanding that
produced the errors, so they cannot detect them.

A parallel attempt to derive expected values from memory failed differently: every
`assert ==` found in the library had `kind=reward`, i.e. it was a leaked answer,
not a specification.

### 4.2 The system cannot say what is worth writing down (V87)

Given a *correct* implementation and asked what convention it embodies, the system
answered `NONE` in **4/4** probes — "the task statement already says this."
It cannot distinguish a specification that came from the task statement from one
that came from the implementation.

The distillation rules make this worse: `_CODE_LINE` / `_IDENTIFIER` filter out
exactly the tokens that carry conventions (`x.strip().lower()`, `"{:,.2f}"`,
`['y','yes','1']`).

### 4.3 The one time we used the system's own output (V86)

Cross-run accumulation was wired and **did** energize: `rewards_seen 0→23→46`,
`guidance` a constant 83 characters. But `facts` grew by **zero** across three runs,
and `per_task.why` was **byte-identical** across all three runs, on all four tasks.
747 characters of code were carried in and changed nothing.

### 4.4 The mechanism

> A judge drawn from the same distribution as the candidate cannot, in expectation,
> distinguish the candidate's errors from the truth. Its discriminative power is
> bounded above by the divergence between the two distributions — which is zero
> when both are the same model, same prompt family, same context.

This is not a tuning problem. Prompt rewording was tested across 54 rounds and
produced effects at or below the shape-noise floor of 0.1290.

---

## 5. Where this leaves the ladder

| Level | Capability | Position | Evidence |
|---|---|---|---|
| L0 execute | write, run, read output | **complete** | 0.9722 on `coding_small` |
| L1 judge | decide correctness, non-vacuously | **complete** | 631 contract / 212 withdrawal / floor 0 |
| L2 measure | know whether it improved, with external ground | **complete** | three arms + placebo + `suite_sha` |
| L3 introspect | find and fix its own errors | **half** | precision 28/28, **recall 0.578** |
| L4 self-improve | write useful knowledge, accumulate across runs | **absent** | self-authored knowledge useful **0 times** |
| L5 autonomy | choose what to do | **absent** | all 67 goals are human-supplied |

L3 is half because the differential self-check is precise but incomplete (V80), and
because the expected-value path is closed (V79). L4 and L5 are absent for the reason
in §4.4.

**"How far from AGI" is the wrong question when asked in points.** `coding_small`
is at 0.9722 and that number says nothing about general intelligence, because all
67 tasks live in one distribution — single pure functions. The honest statement is
qualitative: *two loops are broken and one has never existed, and the breakage is
structural.*

---

## 6. What would have to be true for L4 to work

The only path we can see that does not hit the same wall is **heterogeneous
criteria** — a judge that does not share a distribution with the candidate.
The system already has such judges (the anchor layer, the withdrawal suite) but
they are **human-authored**.

The open question is whether the system can *find* heterogeneous evidence on its own.
The one signal we have that it might: **differential self-check has precision 28/28
(V80)**. Expected outputs come from the task's own tests — external, not self-generated.
Failing that, the system knows *exactly* what differs, even though it only covers 57.8%
of failures.

This is the experiment we have not yet run, and it is where this line of work goes next.

---

## 7. Reproducibility

```
./quickstart.sh          # 631 contract + 97 anchor, zero model, zero network
bash demo/run_demo.sh    # three reference arms: 1.0000 / 0.0000 / 0.6194
bash run_tests.sh        # seven-step gate, full offline
```

All experiments are archived under `bench/evidence/v6_s*/` with `PREREG.md`
(written before the run, never edited after) and `RESULTS.md`. Where a
pre-registration was wrong, the error is recorded as such rather than rewritten.

---

## 8. Limitations

- 67 tasks, single pure functions. No claim beyond this surface.
- One model (`qwen2.5:7b`). A larger model may move L3; it cannot move L4 for the
  reason in §4.4, but we have not tested this.
- The four gates are properties of *this* retrieval implementation. They are
  described as findings about V1, not about retrieval in general.
- Our own pre-registrations were wrong in 8 of 172 rounds, always in the same
  direction: assuming a mechanism found in one cell generalizes to an adjacent cell
  without re-deriving the conditions. Those errors are logged in `DESIGN.md` as
  disciplines 33–38.
