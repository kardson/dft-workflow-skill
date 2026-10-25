# Local geometry and delivery helpers

Use `python SKILL_ROOT/tools/local_tools_cli.py --help` with the selected science Python. Inputs and all new outputs belong in an independent research workspace, outside the installed skill. These helpers never launch VASP, SSH, transfers or monitoring.

| Subcommand | Required inputs | Meaning |
|---|---|---|
| `structure` | `--config CONFIG --output-dir NEW` | Explicit-source unrelaxed bulk/slab/orthogonal interface candidate, mapping and geometry checks |
| `candidates` | Same plus `--maximum-candidates N` | An explicit bounded list of structure configurations; zero jobs |
| `preview` | `--source POSCAR --title TITLE --output-dir NEW` | Cell, original species/pseudo-H labels and per-component T/F flags |
| `report` | `--facts FACTS --review REVIEW --workspace ROOT --output-dir NEW` | Existing controlled analysis consumer first, then standalone HTML with embedded figures |
| `archive` | `--package PACKAGE --archive NEW.zip --receipt NEW.json` | Existing static-package checks, manifest-listed bytes and verified ZIP CRC/members |

Use `templates/local-bulk.json`, `local-slab.json` or `local-interface.json`. Null fields are unresolved choices, not defaults. Specify provenance, source format/path and minimum-separation threshold. Bulk repeats an existing source. Slabs require Miller indices, layers, lateral repeats, both vacuum regions, top/bottom plane trimming, plane tolerance, bottom fixing and a termination description; they are bare unrelaxed cuts. The interface adapter only strains an axis-aligned orthogonal film to the supplied substrate, with explicit shift/gap/vacuum. It reports the imposed strain; it performs no commensurate search, relaxation, surface reconstruction or stability selection.

Fractional/mixed CIF occupations require an explicit scientific model and are rejected. Slab cutting accepts an unconstrained bulk source and establishes the requested new bottom constraints. Original pseudo-H labels, atom origins and direct-cell T/F masks are retained in candidate records. The preview does not evaluate force projections for mixed constraints in oblique cells.

The HTML renderer reuses `analysis.render` to bind facts, review, preparation, parser and original output identities. It embeds PNG plus JSON/CSV up to 2 MB per file; original large files remain referenced. The archiver currently supports validated static packages, excludes licensed PAW/restart data, and creates no transfer permission. Geometry and software checks confer no scientific acceptance.

`vaspkit_wsl_backend.py` is an optional explicit WSL adapter library for VASPKIT 1.5.1 tasks 101/426, using a separate short Linux work directory and a selected ordinary user. No distribution, binary, machine-specific configuration or environment installation is bundled. A receipt distinguishes local WSL from remote calls; process success alone is insufficient when outputs are empty or wrong. Configure and validate the actual target tool before using it.

These pure helper sources can be exported independently. A project's real backend integration, machine configuration and real validation records stay in its private adapter. Preserve the existing full standard environment checks and scientific/execution permissions.
