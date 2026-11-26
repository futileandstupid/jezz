# Interactive Architectural Blueprint (`Sheet A-1` $\leftrightarrow$ `Sheet E-1`)

Live self-hosted **`BlueprintGraph`** extracted deterministically from `jezz` `v0.6.1`'s own AST/CST module graph via `jezz blueprint . --write-html docs/demo/jezz-blueprint.html`.

Use the toolbar inside the CAD frame below (or [open full-screen](demo/jezz-blueprint.html)) to toggle between:
- **Sheet `A-1` — Compartment Floor Plan:** Load-bearing fire-rated compartment walls, module floorplates (`<= 35` per compartment, `<= 100` per package under `JBC-COG-006`), and runtime circuit-breaker counts.
- **Sheet `E-1` — Wiring Schematic:** Directed cross-compartment conduits, guarded perimeter port pins, and external I/O risers verified by `verify_blueprint_lvs`.

<div style="border: 1px solid #1e3a5f; border-radius: 6px; overflow: hidden; margin-top: 1rem;">
  <iframe
    src="demo/jezz-blueprint.html"
    title="Jezz v0.6.1 Switchable CAD Blueprint (Sheet A-1 / E-1)"
    style="width: 100%; height: 760px; border: 0; background: #071324;"
    loading="lazy"
  ></iframe>
</div>
