# `jezz-template-go` — JBC Edition 1.3 Starter

Turnkey **Go (`1.23+`)** starter repository governed by the **Jezz Software Building Code (`JBC` Edition 1.3)** and **Permit-and-Contract Engineering (`PCE`)**, configured via [`jezz.toml`](jezz.toml).

## Architectural Layout (`internal/domain` + `internal/gateway`)

- **`internal/domain/settlement.go`** — Validated wire structs (`Validate() error`), explicit sentinel error discipline, and Z3-discharged `NetAfterFee` (`// @ensures` + range guards).
- **`internal/gateway/wire.go`** — Strict `json.Decoder` with `DisallowUnknownFields()`, trailing-token rejection, and `%w` causal error wrapping.
- **`blueprint.json`** — Sealed `BlueprintGraph` netlist enforced via Layout-vs-Schematic (`LVS`).
- **`internal/gateway/wire_test.go`** — `testing/quick.Check` conservation property suite and unknown-field rejection test.

## Quickstart

```bash
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.0 jezz init my-go-service --scaffold go
cd my-go-service
go vet ./... && go test ./...
```

## Statutory Gates

```bash
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.0 jezz inspect .
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.0 jezz prove . --require-deductive
uvx --from git+https://github.com/futileandstupid/jezz@v0.6.0 jezz blueprint . --verify-target blueprint.json --write-html blueprint.html
```
