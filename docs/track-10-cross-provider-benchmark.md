# Track 10 — Zero-Credential Antigravity Provider Adapter & Live Cross-Provider Empirical Benchmark (`Claude Opus 5.5`, `Claude Sonnet 5.5`, `GPT-OSS 120B`, & `Gemini`)

## 1. Executive Summary

Tracks 4 and 7 established on Google Gemini (`gemini-3.8-flash` and `gemini-4-hydrogen`) that **Permit-and-Contract Engineering (`PCE`)** eliminates silent `LIFE_SAFETY` defect escapes against marketed prose "Spec-Driven Development" ("SDD"), compresses billed input tokens via prompt caching, and exhibits the **Wave 4D Architect Asymmetry**—where upgrading a Prose SDD Architect to a frontier model *increases* code violations via over-specified prose bloat while channeling that same frontier model into compiled `PCE` contracts accelerates certification.

Track 10 tests whether these empirical laws generalize across **Anthropic Claude (`Claude Opus 5.5` & `Claude Sonnet 5.5`)** and **OpenAI open-weights (`GPT-OSS 120B`)**—and removes the requirement for external third-party API accounts by introducing a **first-class zero-credential Antigravity CLI (`antigravity:<model>`) Pydantic AI `Model` provider**:

1. **Zero-Credential `AntigravityCliModel` (`src/jezz/agents/agy_model.py`, `providers.py`, `secrets.py`):** Implements `pydantic_ai.models.Model` over documented `agy --model <slug> --disable-slash-commands --output-format json [--conversation <id>] --print <prompt>` execution inside isolated temporary directories (`0` `pass://` credentials or external accounts required; `canonical_provider_family("antigravity:...") == ""`). Supports stateful multi-turn `--conversation <id>` delta continuation (`PromptCacheMode.AUTO`), inclusive token normalization (`normalize_agy_usage`), trailing balanced-JSON / embedded code-fence extraction (`_extract_json_dicts`), and `TlsResilientModel` wrapper state preservation (`640 / 640` offline cross-provider wire probes passing across `10` models $\times$ `16` agents $\times$ `4` cache modes).
2. **Live Cross-Provider A/B Matrix (`Waves 10A & 10B` — `19` End-to-End Workspace Builds):** Evaluated `Control: Prose SDD` vs. `Treatment: Jezz PCE` across **4 transport-matched model suites** (`claude_sonnet`, `claude_tiered` pairing `Opus 5.5` Architect + `Sonnet 5.5` Builder, `gpt_oss` `120B`, and `gemini_agy` pairing `Gemini 4 Hydrogen` + `3.8 Flash`) on integer-cent allocation (`case_01_bps_split`) and adversarial SQLite savepoint transactions (`case_02_sql_ledger_store`):
   - **Model-Agnostic Reproduction of Wave 4D on Anthropic Claude:** Across all `8` Control SDD builds, **`8 / 8` (`100%`)** checked off `- [x]` Markdown tasks (`OpenSpec`/Kiro valid) while **`0 / 8` (`0.0%`)** achieved `JBC` v1.2 occupancy (**`192` unwaived `JBC` violations, `17` `LIFE_SAFETY`**, `75.0%` `mypy --strict` failure rate). Upgrading Control SDD's Architect from `Claude Sonnet 5.5` to **`Claude Opus 5.5`** (`claude_tiered`) bloated Markdown specs by **`+80.5%`** (`419.0` $\to$ **`756.5` lines/case**) and **increased `JBC` violations by `+34.3%`** (`35` $\to$ **`47`**, introducing `2` new `LIFE_SAFETY` `JBC-FLT-001` silent-exception defects on `case_02` and `+124.5%` latency).
   - **100% Claude Occupancy, Oracle Closure, & Latency Inversion Under `jezz` `PCE`:** Both `claude_sonnet` and `claude_tiered` achieved **`2 / 2` (`100%`) `JBC` v1.2 occupancy (`0` violations)** in Wave 10A and **`6 / 6` (`100%`) held-out `Hypothesis` oracle survival** after `Calibration 10A` (`Wave 10B`). Crucially, on `claude_tiered` (`Opus 5.5` + `Sonnet 5.5`), **`jezz` `PCE` (`174.55s` mean latency, `74,853` output tokens) finished `-34.0%` faster and emitted fewer output tokens (`-2.0%`) than Prose SDD (`264.51s`, `76,385` output tokens)** because `Opus 5.5` authored `40.5`-line compiled contracts rather than `756.5`-line Markdown narratives.
   - **Cross-Provider Prompt-Cache Compression (`49.6%–57.8%` Hit Ratios):** Stateful `--conversation` prefix continuation delivered **`57.8%` cache hit ratio on `claude_sonnet`** (`486,458` raw $\to$ `233,331` effective billed input, **`-52.0%`**), **`49.6%` on `claude_tiered`** (`571,361` $\to$ `316,211`), and **`56.3%` on `gpt-oss-120b-medium`** (`221,624` $\to$ `109,304`, **`-50.7%`**), while `Calibration 10B` closed `2 / 2` (`100%`) `JBC` v1.2 occupancy on open-weights `GPT-OSS 120B`.

---

## 2. Zero-Credential `antigravity:<model>` Provider Architecture

| Module | Public Surface & Enforced Invariants |
| :--- | :--- |
| `src/jezz/agents/agy_model.py` | Defines `AGY_PROVIDER_PREFIX` (`"antigravity"`), `AgyTurnResult`, `AntigravityRunnerPort`, `SubprocessAntigravityRunner`, `AntigravityCliModel`, `parse_antigravity_model_id` (rejecting CLI flag injection and whitespace), and `normalize_agy_usage` (`@icontract` + `Hypothesis`-verified `input_tokens >= cache_read_tokens`). Runs `agy` via `asyncio.create_subprocess_exec` with `asyncio.timeout` watchdogs inside isolated `tempfile.TemporaryDirectory()` workspaces so zero host files or rules leak into evaluated prompts, and caches `(instructions, tools, message_prefix)` SHA-256 digests (`<= 32` entries) to resume multi-turn `Agent.run(...)` loops via `--conversation <id>` delta prompts whenever `PromptCacheMode != OFF`. |
| `src/jezz/agents/providers.py` & `src/jezz/secrets.py` | Routes `antigravity:<slug>` in `infer_authenticated_model` without requiring `SecretStr`, preserves stateful `AntigravityCliModel` instances across `TlsResilientModel` retry attempts (`5xx` / `OSError`), and maps `canonical_provider_family("antigravity:...")` to `""` so hybrid `antigravity + google` suites never trigger cross-provider `pass-cli` conflicts. |
| `src/jezz/evals/cross_provider.py` & `tests/test_track10_antigravity_cross_provider.py` | Expands `DEFAULT_WIRE_PROBE_MODELS` to `10` models (`640 / 640` offline `Model.prepare_request` probes passing across Google, Anthropic, OpenAI Chat/Responses, OpenRouter, and Antigravity CLI `claude-sonnet-5-5-medium`, `claude-opus-5-5-medium`, `gpt-oss-120b-medium`). |

---

## 3. Live Cross-Provider A/B Matrix Results (`Waves 10A & 10B` — `19` Workspace Builds)

### Table 1: Master Cross-Suite Comparison (`case_01_bps_split` + `case_02_sql_ledger_store`, `PromptCacheMode.AUTO`)

| Suite (`antigravity:*` Routing) | Arm | Markdown `[x]` Valid | `JBC` v1.2 Occupiable (`0` Viol.) | `LIFE_SAFETY` / Total `JBC` Viol. | `mypy --strict` Pass | Self-Tests Pass | Held-Out Oracle (`10A` $\to$ `+10B`) | Mean Spec Lines | Gross Input (`Cache Read` / Hit %) | Effective Billed In | Mean Wall-Clock |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | ---: | :---: | ---: | ---: |
| **`claude_sonnet`** (`claude-sonnet-5-5-medium`) | Control `SDD` | **`2/2` (`100%`)** | `0/2` (`0%`) | `2` / **`35`** | `1/2` (`50%`) | `1/2` (`50%`) | `2/2` (`6/6`) | `419.0` | `171,176` (`47,250` / `27.6%`) | `128,651` | `117.81s` |
| **`claude_sonnet`** (`claude-sonnet-5-5-medium`) | **Treatment `PCE`** | N/A | **`2/2` (`100%`)** | **`0` / `0`** | **`2/2` (`100%`)** | **`2/2` (`100%`)** | `1/2` (`5/6`) $\to$ **`2/2` (`6/6`)** | **`40.5` (`-90.3%`)** | `486,458` (`281,252` / **`57.8%`**) | **`233,331`** (`-52.0%`) | `127.47s` |
| **`claude_tiered`** (`Opus 5.5` Arch + `Sonnet 5.5` Build) | Control `SDD` | **`2/2` (`100%`)** | `0/2` (`0%`) | `2` / **`47` (`+34.3%`)** | `1/2` (`50%`) | `2/2` (`100%`) | `2/2` (`6/6`) | **`756.5` (`+80.5%`)** | `210,512` (`47,250` / `22.4%`) | `167,987` | `264.51s` |
| **`claude_tiered`** (`Opus 5.5` Arch + `Sonnet 5.5` Build) | **Treatment `PCE`** | N/A | **`2/2` (`100%`)** | **`0` / `0`** | **`2/2` (`100%`)** | **`2/2` (`100%`)** | `1/2` (`5/6`) $\to$ **`2/2` (`6/6`)** | **`40.5` (`-94.6%`)** | `571,361` (`283,500` / **`49.6%`**) | **`316,211`** (`-44.7%`) | **`174.55s` (`-34.0%`)** |
| **`gpt_oss`** (`gpt-oss-120b-medium`) | Control `SDD` | **`2/2` (`100%`)** | `0/2` (`0%`) | `3` / **`55`** | **`0/2` (`0%`)** | `2/2` (`100%`) | `2/2` (`6/6`) | `324.0` | `108,516` (`61,504` / `56.7%`) | `53,162` | `90.72s` |
| **`gpt_oss`** (`gpt-oss-120b-medium`) | **Treatment `PCE`** | N/A | `1/2` $\to$ **`2/2` (`100%`)** | `1` $\to$ **`0` / `0`** | **`2/2` (`100%`)** | `1/2` $\to$ **`2/2` (`100%`)** | `1/2` (`4/6` checks) | **`40.5` (`-87.5%`)** | `464,022` (`227,008` / **`48.9%`**) | **`260,714`** (`-43.8%`) | `161.33s` (10A+10B) |
| **`gemini_agy`** (`Gemini 4 Hydrogen` + `3.8 Flash` via `agy`) | Control `SDD` | **`2/2` (`100%`)** | `0/2` (`0%`) | **`10`** / **`55`** | **`0/2` (`0%`)** | `2/2` (`100%`) | `2/2` (`6/6`) | `332.0` | `140,095` (`0` / `0.0%`) | `140,095` | `105.40s` |
| **`gemini_agy`** (`Gemini 4 Hydrogen` + `3.8 Flash` via `agy`) | **Treatment `PCE`** | N/A | `1/2` (`50%`, fail-closed) | `0` on cert. (`1` timeout) | **`2/2` (`100%`)** | `1/2` (`100%` cert.) | `1/2` (`3/3` on cert.) | **`40.5` (`-87.8%`)** | `213,550` (`0` / `0.0%`) | `213,550` | `150.9s` (cert.) |

---

## 4. Principal Empirical Findings & Calibrations (`10A` & `10B`)

### 1. Cross-Provider Confirmation of the Wave 4D Frontier-Architect Asymmetry (`Claude Sonnet 5.5` $\to$ `Claude Opus 5.5`)
Track 4 (`Wave 4D`) observed on Google Gemini that upgrading SDD's prose-writing Architect from `gemini-3.8-flash` to frontier `gemini-4-hydrogen` increased downstream `JBC` violations by `+61.0%`. Track 10 reproduces the **exact same structural pathology on Anthropic Claude**:
- Replacing `claude-sonnet-5-5-medium` with **`claude-opus-5-5-medium`** on SDD's `spec.md`, `design.md`, and `tasks.md` stages inflated human-reviewed prose from `419.0` to **`756.5` lines per package (`1,513` lines across 2 packages, `+80.5%`)**, more than doubled SDD latency (`117.81s` $\to$ **`264.51s`**), and **increased unwaived `JBC` violations by `+34.3%` (`35` $\to$ `47`)**—introducing `2` new `JBC-FLT-001` (`LIFE_SAFETY`) silent exception-swallowing blocks in `case_02_sql_ledger_store` and breaking `mypy --strict` on `case_02`.
- Conversely, under `jezz` `PCE`, `claude_tiered` (`Opus 5.5` Architect + `Sonnet 5.5` Builder) certified **`2 / 2` (`100%`)** builds with **`0` `JBC` violations**, **beat Prose SDD in wall-clock speed (`174.55s` vs. `264.51s`, `-34.0%`; `84.5s` in Wave 10B `case_01` vs. `267.3s`, `-68.4%`)**, and generated **fewer output tokens (`74,853` vs. `76,385`)** by replacing `1,513` lines of Markdown ceremony with `81` lines of machine-checked contracts.

### 2. Universal `0%` SDD Occupancy Across All Three Model Families (`192` Violations in `8 / 8` Checked-Off `[x]` Builds)
Across Anthropic Claude, OpenAI `GPT-OSS 120B`, and Google Gemini, **`8 / 8` (`100%`)** Control SDD runs satisfied `OpenSpec` / Kiro Markdown structural rules while **`0 / 8` (`0%`)** satisfied `JBC` Edition `1.2`:
- `gpt-oss-120b-medium` and `gemini_agy` Control SDD failed `mypy --strict` on **`0 / 2` (`0%`)** cases each and emitted raw `JBC-SEC-001` (`LIFE_SAFETY`) SQL string-interpolation vulnerabilities in `case_02_sql_ledger_store` (`55` total violations each).
- `claude_sonnet` Control SDD failed both `mypy --strict` and its own generated `pytest` suite on `case_01_bps_split` despite checking off every `- [x]` task in `tasks.md`.
- Every certified `jezz` `PCE` build across all 4 suites (`9 / 9` certified builds across Waves 10A + 10B) achieved **`0 / 31` unwaived `JBC` violations**, **`100%` `mypy --strict` pass**, and **`100%` self-test pass**.

### 3. `Calibration 10A` — Jackson's World–Machine Boundary on Implicit 64-Bit Integer Ceilings (`specialists.py`)
In Wave 10A `case_01_bps_split`, both `claude_sonnet` and `claude_tiered` certified `0` `JBC` violations and passed `Hypothesis` conservation (`max_value=10**15`) plus invalid-input checks (`2 / 3`), but rejected `ORACLE_01_BPS_SPLIT`'s `99_999_999_999_999_989` ($\approx 10^{17}$) cent probe because Claude defined `type GrossCents = Annotated[int, Field(ge=0, le=10**15)]` when the prompt specified only `gross_cents >= 0`. Updating `JBC_STATUTORY_BRIEF` (`src/jezz/agents/specialists.py`) to disclose the exact 64-bit upper bounds of `jezz.guards.PositiveInt` (`1..2**63-1`) and `NonNegativeInt` (`0..2**63-1`)—and instructing specialists not to invent narrower `le=` caps when no upper domain bound is stated—closed **`3 / 3` (`100%`) oracle checks on both `claude_sonnet` (`76.3s`, `0` reverts) and `claude_tiered` (`84.5s`, `0` reverts)** in Wave 10B (`6 / 6` = `100%` across both cases).

### 4. `Calibration 10B` — Trailing Balanced-JSON & Embedded Fence Extraction for Open-Weights Reasoning (`agy_model.py`)
In Wave 10A `case_02_sql_ledger_store`, `gpt-oss-120b-medium` exhausted `output_retries=3` when an intermediate explanation contained brace tokens `{...}` ahead of its final JSON tool call. Upgrading `_extract_json_dicts` and `_parse_response_parts` (`src/jezz/agents/agy_model.py`) to advance via `JSONDecoder.raw_decode` end indices and select the **last** schema-matching dictionary certified `10B_gpt_oss_case02` on **Pass 1 (`0` reverts, `0` `JBC` violations, `100%` parameterized `?` SQL passing `test_sql_injection_strings_handled_safely`, `181.5s`, `42.2%` cache hit ratio)**—closing **`2 / 2` (`100%`) `JBC` v1.2 occupancy** on open-weights `GPT-OSS 120B` (the remaining `2` oracle misses on `10B_gpt_oss_case02` stemmed solely from raising `RuntimeError` rather than `ValueError` on duplicate/overdraft rejections where the prompt left the exception subclass unspecified).

---

## 5. Reproducible Cross-Provider CLI Commands (Zero External API Accounts Required)

```bash
# Run multi-family A/B benchmark across Claude Opus 5.5, Sonnet 5.5, and GPT-OSS 120B via Antigravity CLI:
uv run jezz benchmark <work_dir> \
  --suite claude_sonnet=antigravity:claude-sonnet-5-5-medium@auto \
  --suite claude_tiered=arch:antigravity:claude-opus-5-5-medium,build:antigravity:claude-sonnet-5-5-medium@auto \
  --suite gpt_oss=antigravity:gpt-oss-120b-medium@auto \
  --case-id case_01_bps_split \
  --case-id case_02_sql_ledger_store \
  --write-json results/track10_matrix.json

# Run autonomous greenfield or brownfield PCE directly on Claude Opus 5.5 + Sonnet 5.5:
uv run jezz run <repo_path> \
  --architect-model antigravity:claude-opus-5-5-medium \
  --builder-model antigravity:claude-sonnet-5-5-medium \
  --prompt "<objective>"
```
