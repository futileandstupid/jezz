# Pillar 3A — Forensic Teardown of Industry "Spec-Driven Development" ("SDD")

## 1. Thesis: Automated Agile Backlog Theatre Disguised as a Paradigm Shift

Throughout 2025–2026, tool vendors—led by GitHub (`github/spec-kit`), Fission AI (`Fission-AI/OpenSpec`), and AWS (`Kiro`)—marketed **"Spec-Driven Development" ("SDD")** as a foundational breakthrough in software engineering. Their shared pitch asserts that software engineering historically treated source code as the sole source of truth while treating specifications as disposable scaffolding, and that instructing an LLM to generate multi-file Markdown documents (`spec.md`, `requirements.md`, `design.md`, `tasks.md`) before generating code constitutes a historic *"Power Inversion."*

Evaluated against primary computer-science literature, information theory, and civil/structural engineering standards, marketed "SDD" fails on three independent counts:

1. **Historical Erasure & Squandered Economics:** It erases **55+ years** of formal specification, Design by Contract (DbC), model checking, Model-Driven Engineering, property-based testing, and executable BDD—conflating informal Product Requirements Documents (PRDs) with engineering specifications, while squandering the exact economic breakthrough coding agents actually unlock.
2. **Compounding Stochastic Drift & Specification Laundering:** Far from transcending Agile, these tools chain multiple unverified LLM prose-expansion hops atop Agile's worst primitives (persona user stories, backlog tickets, `- [x]` velocity checkboxes), amplifying assumption drift and laundering unverified AI prose through human review fatigue.
3. **Absence of a Soundness or Code-Enforcement Relation ($\mathcal{I} \models \mathcal{S} \wedge \mathcal{C}$):** English Markdown is ambiguous, non-compositional, and undecidable. None of the three tools compile specifications into static/runtime invariants or enforce deterministic software building codes; "compliance" reduces to system-prompt begging (`constitution.md` / steering files) and an LLM toggling `- [ ]` to `- [x]`.

---

## 2. Primary-Source Autopsy of the Three Offerings

### Exhibit A — GitHub `spec-kit` (`github/spec-kit`)

In `spec-driven.md` (the philosophical manifesto at the root of `github/spec-kit`), GitHub frames its thesis under **"The Power Inversion"**:

> *"For decades, code has been king. Specifications served code—they were the scaffolding we built and then discarded once the 'real work' of coding began. We wrote PRDs to guide development, created design docs to inform implementation, drew diagrams to visualize architecture. But these were always subordinate to the code itself. Code was truth. Everything else was, at best, good intentions."*
> — `github/spec-kit`, `spec-driven.md` ("The Power Inversion")

> *"Spec-Driven Development (SDD) inverts this power structure. Specifications don't serve code—code serves specifications. The Product Requirements Document (PRD) isn't a guide for implementation; it's the source that generates implementation. Technical plans aren't documents that inform coding; they're precise definitions that produce code."*
> — `github/spec-kit`, `spec-driven.md`

In the accompanying GitHub Blog announcement (*"Spec-driven development with AI: Get started with a new open source toolkit"*, Den Delimarsky, Sept 2, 2025), GitHub claims:

> *"That's why we're rethinking specifications — not as static documents, but as living, executable artifacts that evolve with the project."*

#### Forensic Findings on `spec-kit`
1. **The PRD Category Error:** `spec-driven.md` gives the game away in its own words: *"We wrote PRDs to guide development... The Product Requirements Document (PRD) isn't a guide for implementation; it's the source that generates implementation."* A PRD is a **product-management wishlist**, not an engineering specification. Treating PRDs as the historical ceiling of software specification confuses product discovery with formal design.
2. **Fraudulent Redefinition of "Executable" & The Non-Reproducibility Proof:** In computer science, an *executable specification* possesses formal operational or denotational semantics evaluated by a deterministic interpreter, typechecker, solver, or contract monitor. Under the hood, `spec-kit` contains **zero** parser, type system, or solver: `specify init` copies Markdown templates and shell helpers into `.specify/`, and `/speckit.specify` $\to$ `/speckit.plan` $\to$ `/speckit.tasks` $\to$ `/speckit.implement` are **chat slash-prompts** feeding English Markdown into a probabilistic LLM.
   - **Empirical falsification of the "source" claim:** Run `/speckit.implement` twice on the exact same `spec.md` and `plan.md` across two sessions or models. You obtain **two structurally incompatible codebases** with different internal types, different failure modes, and different edge-case bugs—because English prose is massively under-determined. `spec.md` is not "source that generates implementation" the way C compiles to assembly; it is a **writing prompt**.
3. **Constitutional Prompt-Begging (`.specify/memory/constitution.md`):** `spec-kit` ships a nine-article "Constitution" with prose placeholders (*"Articles IV, V, and VI are intentionally defined by each project's constitution... teams replace those placeholders with the principles that match their system"*). Enforcement consists solely of pasting `constitution.md` into the prompt context and hoping the model obeys. Under context pressure, LLMs routinely violate prose constitutions—injecting `Any`, swallowing exceptions, or writing vacuous tests—with zero deterministic gate to stop them.
4. **Why `/speckit.clarify` and `/speckit.analyze` Do Not Rescue It:** Defenders point to `spec-kit`'s optional `/speckit.clarify` and `/speckit.analyze` slash commands as quality gates. Both are merely **LLM-reads-LLM prose passes**: neither is enforced by a state-machine dominance lock (nothing blocks `/speckit.implement` when ambiguities remain), and asking the same stochastic model class to eyeball 1,000 lines of its own prose is QA theatre, not formal consistency checking.

---

### Exhibit B — Fission AI `OpenSpec` (`Fission-AI/OpenSpec`)

`OpenSpec` (*"Spec-driven development for AI coding assistants"*) positions itself as a lightweight, brownfield-friendly workflow organizing artifacts into `openspec/specs/` and `openspec/changes/<id>/` (`proposal.md`, `design.md`, `tasks.md`, and delta files containing `## ADDED Requirements`, `## MODIFIED Requirements`, `## REMOVED Requirements`), gated by `openspec validate --strict`.

#### Forensic Findings on `OpenSpec`
1. **Cargo-Cult BDD (Gherkin Severed from Execution):** `OpenSpec` requires every `### Requirement:` heading to include RFC 2119 modals (`SHALL`/`MUST`) and at least one `#### Scenario:` block with `GIVEN / WHEN / THEN` prose bullets.
   - When Dan North introduced Behavior-Driven Development (2006) and Aslak Hellesøy created Cucumber/Gherkin, the entire mechanism rested on **binding every `Given / When / Then` step to executable test code**; if the code violated the scenario, execution failed.
   - `OpenSpec` **severs the scenario from the test runner**. `openspec validate --strict` parses *only the Markdown files*: it checks whether the Markdown headings match expected regexes, whether `MODIFIED` headings match titles in `openspec/specs/`, and whether a non-empty `#### Scenario:` block appears underneath. It **never parses, imports, typechecks, or executes a single line of source or test code**.
2. **`[x]` Checkbox Compliance Theatre:** `openspec validate --archived` verifies completion by scanning `tasks.md` to confirm every `- [ ]` checkbox was toggled to `- [x]`. An agent can leave `eval()` injection sinks, hollow `pass` stubs, or `assert True` tests across `src/`, mark every item `- [x]` in `tasks.md`, and `openspec validate --strict --archived` exits `0` (`Change is valid`).
3. **The "We Also Run `pytest` in CI" Epiphenomenal Trap:** Defenders argue teams pair `openspec validate` with `pytest` in CI. That defense indicts `OpenSpec` rather than saving it: because `OpenSpec` maintains **zero AST or symbol binding** between `### Requirement:` prose and `test_*` functions, `pytest` does 100% of the real verification while `openspec/specs/` can drift 180° out of sync with what `pytest` actually tests—leaving both CI jobs green over a contradicted specification.

---

### Exhibit C — AWS Kiro (`kiro.dev`)

AWS Kiro markets an "agentic IDE" moving developers *"from vibe coding to viable code"* via three Markdown files in `.kiro/specs/<feature>/` (`requirements.md`, `design.md`, `tasks.md`), `.kiro/steering/*.md` files, and event-driven **Agent Hooks**.

Its `requirements.md` template mandates:
```text
### Requirements 1: **User Story:** As <persona>, I want ..., so that I...

### Acceptance Criteria
1. WHEN [condition/event] THE SYSTEM SHALL [expected behavior]
```

#### Forensic Findings on AWS Kiro
1. **Automating Agile Ceremony:** Kiro's atomic unit is Mike Cohn's 2004 Scrum User Story (`As <persona>, I want ..., so that ...`) bolted onto Rolls-Royce's **EARS** (*Easy Approach to Requirements Syntax*, Mavin et al., 2009). At Rolls-Royce, EARS was a constrained-English pre-formal step feeding downstream DO-178B/C verification matrices—never a self-verifying endpoint. Wrapping EARS inside Agile persona stories and `tasks.md` checkboxes institutionalizes 1990s prose Waterfall + 2005 Scrum backlog grooming inside an IDE sidebar.
2. **Unverified Architectural Drift (`design.md`):** Kiro generates prose and Mermaid diagrams in `design.md` with **zero static analyzer** checking that emitted classes obey the diagrammed module DAG, preserve `LCOM4 == 1` cohesion, or enforce boundary schemas.
3. **Why Kiro "Agent Hooks" Fail as Safety Gates:** Kiro defenders cite Agent Hooks (triggers firing on file save/create—e.g., *"when a file is saved, update tests or check docs"*). Kiro Hooks trigger **another non-deterministic LLM prompt** on save: burning tokens stochastically with no formal pass/fail lattice, no rollback dominance guarantee, and nothing preventing the hook's LLM from weakening a test to match broken code (`JBC-VRF-004`).

---

## 3. Count I — The 55-Year Lineage & The Squandered Economic Unlock

### A. Primary Engineering Milestones Omitted by "SDD"

| Year | Primary Work | What It Established (Omitted by Marketed "SDD") |
| :---: | :--- | :--- |
| **1967–1969** | R. W. Floyd; C. A. R. Hoare (*An Axiomatic Basis for Computer Programming*) | Axiomatic preconditions and postconditions $\{P\}\ C\ \{Q\}$ proving correctness relative to logical assertions rather than prose intent. |
| **1972–1977** | D. L. Parnas (*Information Hiding* & U.S. Navy A-7E Flight Software Specs) | Mathematical tabular specifications and acyclic module secrets deployed in production avionics. |
| **1977–1978** | J.-R. Abrial (**Z**); Bjørner & Jones (**VDM**) | Set-theoretic state schemas, invariants, and formal refinement proofs from specification to implementation. |
| **1986–1997** | Bertrand Meyer (**Eiffel** & *Object-Oriented Software Construction*) | **Design by Contract (`require`, `ensure`, `invariant`)**: specifications compiled directly into the language—eliminating spec/code drift by construction. |
| **1999** | Leslie Lamport (**TLA+**) | Temporal logic state-machine specs and model checking—used in production **by AWS itself** for S3 and DynamoDB (Newcombe et al., *CACM* 2015), yet ignored by AWS Kiro's marketing. |
| **1999–2000** | Kent Beck (**TDD**); Claessen & Hughes (**QuickCheck**) | Executable falsification suites and universally quantified property generators ($\forall x \in D: P(f(x))$). |
| **2001–2002** | OMG (**MDA / MDD**); Daniel Jackson (**Alloy**) | Formal metamodels and SAT-backed relational model finding over bounded scopes. |
| **2006–2008** | Dan North (**BDD**); Aslak Hellesøy (**Cucumber**) | Executable acceptance specifications wired directly to automated test runners. |
| **2010–2014+** | **SPARK Ada**, **Dafny**, **Frama-C**, **Lean / Coq** | SMT-discharged pre/postconditions, loop variants, and machine-checked proofs of absence of runtime errors. |

### B. Steelmanning—and Demolishing—the "Commercial Adoption" Defense
An honest defender of `spec-kit` will object: *"When `spec-driven.md` says 'code was king and PRDs were discarded,' it was describing **mainstream commercial software practice**, not denying that Hoare or Lamport existed in academia! Formal methods never became mainstream because the human authoring cost of writing formal specs was too high."*

Conceding that historical observation makes the indictment of marketed SDD **far worse**:
- Formal contracts, strict domain types, and property-based generators stalled in mainstream CRUD teams for **one economic reason**: humans found hand-authoring formal invariants tedious under deadline pressure.
- **Coding agents collapse the authoring cost of formal contracts, bounded schemas, and property generators toward zero**—and agents excel when constrained by immediate, deterministic compiler/validator feedback.
- Faced with the exact technology capable of making Meyer's DbC, QuickCheck properties, and civil-grade static building codes ubiquitous at near-zero human labor cost, `spec-kit`, `OpenSpec`, and Kiro **went backward**—using LLMs to mass-produce 1990s prose PRDs and Agile checklists that failed thirty years ago.

---

## 4. Count II — Information-Theoretic Drift, Specification Laundering, & Concrete Failure Proof

### A. Multi-Hop Stochastic Drift vs. Clamped Oracles
`spec-kit` and Kiro interpose three unverified natural-language generation hops between human intent $I_0$ and executable code $C$:

$$I_0 \xrightarrow{\text{LLM}} S_{\text{prose}} \xrightarrow{\text{LLM}} D_{\text{prose}} \xrightarrow{\text{LLM}} T_{\text{prose}} \xrightarrow{\text{LLM}} C_{\text{code}}$$

By the Data Processing Inequality ($I(I_0; T_{\text{prose}}) \le I(I_0; S_{\text{prose}})$), an ungrounded stochastic transformation chain **cannot create fidelity to $I_0$**—each prose-to-prose hop can only lose precision or inject hallucinated assumptions ($\epsilon_k$). Even at a generous $90\%$ assumption-fidelity rate per prose stage, a four-hop unverified chain degrades to $0.90^4 \approx 65.6\%$ fidelity—while producing **1,000+ lines of authoritative-looking Markdown** that exhaust human reviewers.

Independent empirical testing already confirms this review pathology. Evaluating `spec-kit` and Kiro on *MartinFowler.com* (*"Understanding Spec-Driven-Development: Kiro, spec-kit, and Tessl"*, 2025), Thoughtworks principal engineer Birgitta Böckeler reported:

> *"spec-kit created a LOT of markdown files for me to review. They were repetitive, both with each other, and with the code that already existed. Some contained code already. Overall they were just very verbose and tedious to review... To be honest, I'd rather review code than all these markdown files."*

That phenomenon is **Specification Laundering**: multiplying 40 words of prompt intent into 1,200 lines of tedious prose until the exhausted human rubber-stamps *Approve*, laundering the LLM's silent errors into "the official spec."

### B. Concrete Failure Exhibit: `OpenSpec` / Kiro "Green" vs. `jezz` `RED_TAG`

Consider a routine task: *"Implement an account transfer function."*

**1. Marketed SDD Artifacts (`OpenSpec` / Kiro):**
- `spec.md` / `requirements.md`:
  ```markdown
  ### Requirement: Account Funds Transfer
  **User Story:** As an account holder, I want to transfer money, so that I can pay peers.
  WHEN a valid transfer is requested THE SYSTEM SHALL debit the sender and credit the recipient.
  #### Scenario: Successful transfer
  - GIVEN sender has $100 and recipient has $50
  - WHEN sender transfers $25
  - THEN sender has $75 and recipient has $75
  ```
- `tasks.md`: `- [x] 1.1 Implement transfer_funds and unit test`
- Generated Python implementation:
  ```python
  def transfer_funds(db, sender_id, recipient_id, amount: float):
      try:
          db.execute(f"UPDATE accts SET bal = bal - {amount} WHERE id = '{sender_id}'")
          db.execute(f"UPDATE accts SET bal = bal + {amount} WHERE id = '{recipient_id}'")
          return True
      except Exception:
          pass
      return False


  def test_transfer_funds():
      assert True
  ```
- **SDD Outcome:** `openspec validate --strict --archived` exits **`0` (`Change is valid`)**! Every Markdown header, `SHALL` keyword, `GIVEN/WHEN/THEN` block, and `- [x]` checkbox is 100% compliant. Yet the code contains **five catastrophic defects**:
  1. **Negative-transfer theft:** Passing `amount = -1000.0` *credits* the attacker `+1000.0` and drains the victim.
  2. **`NaN`/`Inf` & IEEE-754 float corruption:** Passing `float("nan")` or `0.1 + 0.2` corrupts ledger balances permanently.
  3. **SQL Injection (`CWE-89`):** Unparameterized f-string `db.execute(f"...")`.
  4. **Silent Partial-Failure Corruption (`CWE-390`):** If query 2 fails after query 1 succeeds, `except Exception: pass` swallows the fault and vanishes money.
  5. **QA Theatre:** `test_transfer_funds` asserts `True`.

**2. `jezz` (`PCE` + `JBC` v1.2) Outcome on the Exact Same Code:**
`BuildingInspector` and `audit_pce_dossier` immediately issue a **`RED_TAG`** across **seven deterministic citations**—with zero LLM judgment involved:
- `JBC-DBC-002` (unannotated `db`, `sender_id`, `recipient_id`, and return type)
- `JBC-DBC-004` (missing `@jezz_callable` runtime signature/return breaker)
- `JBC-DBC-005` (unbounded `float` instead of positive integer `PositiveInt` cents)
- `JBC-SEC-001` (`LIFE_SAFETY`: f-string SQL injection sink)
- `JBC-FLT-001` (`LIFE_SAFETY`: silent `except Exception: pass` fault suppression)
- `JBC-VRF-001` (`LIFE_SAFETY`: tautological `assert True` test theatre)
- `PCE` Mandate Gate (Agile `"As an account holder, I want..."` and `- [x]` theatre rejected at `Phase I`).

---

## 5. Adversarial Red-Team FAQ: Answering Objections Against `PCE`

### Objection 1: *"Doesn't `PCE` still start from an informal English user prompt—so can't an agent encode the wrong formal contract at `SPECIFY`?"*
No system can formally prove equivalence between unstated human thoughts and a mathematical model (Jackson's *World–Machine* boundary). However, `PCE` attacks that boundary across **four structural asymmetries** where prose SDD collapses:
1. **Symbolic Compression vs. Prose Inflation:** SDD expands 40 words into 1,200 lines of prose where wrong assumptions hide in paragraphs. `PCE` compresses intent into **~20 lines of symbolic types and `@icontract` predicates** (`amount_cents: PositiveInt`, `balance_cents >= amount_cents`), making boundary choices (`>` vs. `>=`) starkly visible for 30-second human inspection.
2. **Mandatory `BHV-02 ELICIT` Dominance:** Unresolved domain questions populate `open_ambiguities`, which **hard-blocks** `EngineeringMandate` from advancing to `PERMIT` or `CONSTRUCTION`.
3. **Intent-Independent Building Codes (`JBC`):** Even if a human prompt is terse on domain edge cases, **universal `JBC` safety codes do not depend on user prompts**. No user ever "intended" SQL injection, `NaN` floats, unbounded `while` loops, silent `except: pass`, or `LCOM4 == 4` God classes—`JBC` outlaws them unconditionally.
4. **Adversarial Counterexample Search (`Hypothesis`):** Property generators actively search boundary values (`0`, `-1`, `""`, max bounds) during `VERIFY_DYNAMIC` to falsify bad assumptions empirically.

### Objection 2: *"Is all prose useless? What about Architecture Decision Records (ADRs)?"*
`PCE` distinguishes **Design Rationale (*Why*)** from **Normative Specification (*What*)**:
- **Rationale (*Why*):** Explaining *why* an architectural trade-off or `CodeVariance` was approved by human authority belongs in concise rationale fields (`BehaviorNode.rationale`, `CodeVariance.justification`).
- **Specification (*What*):** Behavioral invariants, schemas, module boundaries, and safety rules must **never** live exclusively in prose—every `DomainInvariant` in `PCE` must bind to a compiled `verification_symbol` verified in the workspace AST/CST (`UNBOUND_INVARIANT_SYMBOL`).

---

## 6. Comparative Engineering Matrix

| Dimension | Agile / Scrum | Marketed "SDD" (`spec-kit`, `OpenSpec`, Kiro) | `jezz` Permit-and-Contract Engineering (`PCE`) |
| :--- | :--- | :--- | :--- |
| **Primary Unit of Intent** | Persona User Story (`As a..., I want...`) | LLM-inflated Markdown (`spec.md`, `requirements.md`) | Typed `EngineeringMandate` (~20 lines) + symbol-linked `DomainInvariant`s |
| **Information Trajectory** | Unrecorded verbal drift | 4-hop stochastic prose amplification ($0.9^4$ drift) | Single `ELICIT` disambiguation $\to$ deterministic zero-entropy compiler/AST gates |
| **Ambiguity Handling** | Deferred to "sprint conversation" | Optional LLM prose review (`/speckit.clarify`); guesses buried in text | Hard state-machine halt at `BHV-02 ELICIT` when `open_ambiguities` is non-empty |
| **Universal Safety Standards** | Ad-hoc PR review comments | `constitution.md` / steering / LLM hooks (prompt begging) | 31-provision `JBC` v1.2 `CODE_BOOK` enforced deterministically across Python, TS, Rust, & Go AST/CST, graph, & runtime |
| **Domain Specification Medium** | Sticky notes / Jira prose | Dead Markdown (`#### Scenario:` severed from test runner) | Compiled schemas, contracts, & property suites (`STRICT_FROZEN_CONFIG`/`zod`/`serde`, `@jezz_callable`, `@icontract`, `hypothesis`/`vitest`/`cargo test`/`go test`) |
| **Task / Workflow Topology** | Kanban columns & velocity points | Flat `- [ ]` checkboxes in `tasks.md` | Verified 21-behavior `BehaviorGraph` with Kildall fixed-point dataflow proofs |
| **Completion Criterion** | Demo passes happy path | LLM edits `[ ]` $\to$ `[x]` (`openspec validate` exits `0`) | Cryptographically sealed `CertificateOfOccupancy` (`RED_TAG` forces `BHV-20 REVERT`) |
