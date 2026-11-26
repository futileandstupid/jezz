# Security Policy

## Supported Versions

Security fixes are issued against the latest minor release line on `main`:

| Version | Supported |
| :--- | :---: |
| `0.6.x` | Yes |
| `< 0.6.0` | No |

## Reporting a Vulnerability

Do **not** open public GitHub issues for suspected security vulnerabilities.

Report vulnerabilities privately through **[GitHub Private Vulnerability Reporting](https://github.com/futileandstupid/jezz/security/advisories/new)**. Include:

1. Affected component (`jezz.codes`, `jezz.codes.smt`, `jezz.agents`, `jezz.mcp_server`, `jezz.secrets`, `action.yml`, or `@jezz/blueprint`).
2. Minimal deterministic reproduction input (source snippet, mandate/dossier JSON, or CLI invocation).
3. Impact assessment (e.g., `ContainedPath` traversal escape, subprocess injection, credential URI leak, or `LIFE_SAFETY` false-negative bypass).

Maintainers acknowledge reports within 7 days and publish coordinated advisories once a verified fix is released.

## Security & Soundness Scope

- **Path Containment & Subprocess Execution (`JBC-SEC-001`, `JBC-SEC-005`, `JBC-SEC-006`):** Workspace file reads/writes are confined via `ContainedPath`, and dynamic compiler/test gates execute `subprocess` argv tuples with `shell=False` and bounded timeouts. Any bypass of `ContainedPath` symlink/traversal confinement or shell/argument injection in `jezz` or `action.yml` is treated as critical.
- **Credential URI Isolation (`JBC-SEC-002`):** `jezz.secrets` resolves credentials exclusively via `<scheme>://` providers (`env://`, `pass://`, `op://`, `keychain://`) without persisting raw secrets into `EngineeringDossier` or telemetry records.
- **Static Inspection & Bounded Z3 Verification:** `jezz inspect` performs deterministic AST/CST and monotone call-flow analysis, while `jezz prove` discharges quantifier-free contracts (`QF_LIA` / `QF_NRA` / `QF_SLIA`) within explicit statement and solver budgets—classifying opaque callables conservatively as `BOUNDED_BY_PBT` rather than claiming deductive proof.
