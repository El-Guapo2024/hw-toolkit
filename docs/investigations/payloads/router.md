# Router Payload Investigation: FreeRouting + OrthoRoute Integration

## Overview

This document validates the Pydantic contract in our router-mcp integration against real Specctra DSN/SES payloads and actual HTTP APIs for FreeRouting and OrthoRoute. The goal is to confirm our contract carries enough information to drive auto-routing without guessing.

**Status**: Both engines ARE self-hosted (hw-router-service + hw-orthoroute-service Docker containers). No external APIs. Full control over the wire format.

---

## 1. Specctra DSN Format (Prespective from KiCad Export)

Real example snippet from FreeRouting's public Issue026-J2_reference.dsn fixture:

```lisp
(pcb "/path/to/board.dsn"
  (parser
    (string_quote ")
    (space_in_quoted_tokens on)
    (host_cad "KiCad's Pcbnew")
    (host_version "5.1.5-52549c5~86~ubuntu16.04.1")
  )
  (resolution um 10)
  (unit um)
  (structure
    (layer F.Cu
      (type signal)
      (property
        (index 0)
      )
    )
    (layer B.Cu
      (type signal)
      (property
        (index 1)
      )
    )
    (boundary
      (path pcb 0  153035 -98425  106045 -98425  106045 -73025  153035 -73025
            153035 -98425)
    )
    (via "Via[0-1]_800:400_um")
    (rule
      (width 250)
      (clearance 200.1)
      (clearance 200.1 (type default_smd))
      (clearance 50 (type smd_smd))
    )
  )
  (placement
    (component "TE_1888019-6:TE_1888019-6"
      (place J2 135255 -85725 front 90 (PN "1888019-6"))
    )
    (component "TE_1-84953-5:TE_1-84953-5"
      (place U1 111410 -85550 front 90 (PN "1-84953-5"))
    )
  )
  (library
    (image "TE_1888019-6:TE_1888019-6"
      (outline (path signal 127  -9050 8600  9050 8600))
      (outline (path signal 127  9050 8600  9050 -16800))
      ...
      (pin Round[A]Pad_1358_um S1 -8900 -5720)
      (pin Rect[T]Pad_350x1800_um A1 -6600 5180)
      ...
      (keepout "" (circle F.Cu 1590 -8000 0))
    )
    (image "TE_1-84953-5:TE_1-84953-5"
      ...
    )
    (padstack Round[A]Pad_1358_um
      (shape (circle F.Cu 1358))
      (shape (circle B.Cu 1358))
      (attach off)
    )
    (padstack Rect[T]Pad_350x1800_um
      (shape (rect F.Cu -175 -900 175 900))
      (attach off)
    )
    ...
  )
  (network
    (net GND
      (pins J2-S1 J2-A1 U1-1)
    )
    (net VCC
      (pins U1-2 U1-3)
    )
    (class GND
      (clearance 200.1)
      (trace_width 250)
      (via_width 800 400)
    )
    (class VCC
      (clearance 150)
      (trace_width 500)
      (via_width 1200 400)
    )
  )
  (wiring)
)
```

**Key fields for routing**:
- `(resolution um 10)` — DSN units (micrometers, 10-unit grid)
- `(unit um)` — coordinate units
- `(structure)` — layer definitions, board boundary, global rule (default trace width 250 um, clearance 200.1 um)
- `(via "Via[0-1]_800:400_um")` — via class: 800 um drill, 400 um pad
- `(placement)` — component XY positions and rotations
- `(library)` — padstack definitions (pad shapes per layer), footprint geometry (outlines, keepouts)
- `(network)` — nets and net classes with per-class rules:
  - `(clearance ...)` — minimum clearance (in DSN units)
  - `(trace_width ...)` — preferred trace width
  - `(via_width ...)` — via outer diameter and drill diameter
- `(wiring)` — empty pre-route; filled by router with `(wire ...)` and `(via ...)` entries

---

## 2. Specctra SES Format (FreeRouting Output)

Real SES structure (from FreeRouting's output spec):

```lisp
(session
  (base_design "board.dsn")
  (placement
    (component J2 135255 -85725 front 90)
    (component U1 111410 -85550 front 90)
  )
  (was_is
  )
  (routes
    (resolution um 10)
    (network_out GND
      (wire
        (path F.Cu 250  155000 -80000  160000 -80000)
      )
      (wire
        (path B.Cu 250  140000 -75000  145000 -75000)
      )
      (via Via[0-1]_800:400_um  145000 -75000)
    )
    (network_out VCC
      (wire
        (path F.Cu 500  120000 -85000  125000 -85000)
      )
      (via Via[0-1]_800:400_um  125000 -85000)
    )
  )
  (wiring_edit_history
  )
)
```

**Key fields**:
- `(session)` root — references the source DSN via `(base_design ...)`
- `(placement)` — final component positions (can differ from input; FreeRouting may adjust for routing)
- `(routes)` — routed connectivity per net
  - `(network_out <netname> ...)` — all wires and vias for one net
  - `(wire (path <layer> <width> x1 y1 x2 y2 ...))` — trace segment on a layer with width in DSN units
  - `(via <padstack_name> x y)` — via placement at a coordinate

**Contract**: Our pcb_writer.run_ses_import parses this and replays the routes back into KiCad's .kicad_pcb file via IPC.

---

## 3. FreeRouting API (hw-router-service)

Self-hosted FastAPI service. Source: `/hw-router-service/src/hw_router_service/server.py`.

### HTTP Contract

**POST /jobs** — Submit routing job
```python
# Request body (application/json)
{
  "dsn": "<string>",      # Full Specctra DSN text (not a path)
  "passes": 5,            # Router pass count: 1-200, default 5
  "threads": 0            # Parallelism hint: 0=FreeRouting default, 1-64 otherwise
}

# Response
{
  "job_id": "abc12def34",  # Short UUID (12 hex chars)
  "state": "queued"        # Will be: queued → running → succeeded/failed/cancelled
}
```

**GET /jobs/{job_id}** — Poll status
```python
# Query params
?log_tail=50             # Lines of log to include (default 50, set 0 to omit)
&include_ses=false       # Fetch SES inline when state=succeeded (default true)

# Response
{
  "job_id": "abc12def34",
  "state": "running",     # queued | running | succeeded | failed | cancelled
  "elapsed_s": 3.45,
  "error": null,
  "log_tail": "Auto-router pass #1 ... score of 123.45 (2 unrouted)\n...",
  "ses": null or "<session ... >"  # Inline SES when state=succeeded + include_ses=true
}
```

**POST /jobs/{job_id}/cancel** — Cancel a queued/running job
```python
# Response
{
  "ok": true,
  "job_id": "abc12def34",
  "state": "cancelled"
}
```

**GET /health** — Service readiness
```python
{
  "ok": true,
  "java": "/usr/bin/java",
  "freerouting_jar": "/path/to/freerouting.jar",
  "queue": {"name": "router", "queued": 2}
}
```

### Job Lifecycle in Our Code

From `/mcp_server/router/engines.py`:
1. **Preflight**: `GET /health` to confirm service is up (fail-fast before DSN export)
2. **Submit**: `POST /jobs` with full DSN text (reads from disk via `dsn_path.read_text()`)
3. **Poll**: `GET /jobs/{job_id}?include_ses=false&log_tail=20` repeatedly until `state in ("succeeded", "failed", "cancelled")`
4. **Fetch result**: `GET /jobs/{job_id}?include_ses=true` to download SES once done
5. **Cancel**: If user cancels or timeout, `POST /jobs/{job_id}/cancel` to stop remote JVM

---

## 4. OrthoRoute API (hw-orthoroute-service)

Alternative GPU-accelerated engine. Source: `/mcp_server/router/engines.py` `route_orthoroute()`.

### HTTP Contract

**POST /jobs** — Submit routing job (multipart upload)
```python
# Request: multipart/form-data
{
  "pcb": <binary .kicad_pcb file>,
  "passes": "5",         # String (form data)
  "timeout_s": "1800"    # String (form data)
}

# Response (application/json)
{
  "job_id": "abc12def34",
  "state": "queued"
}
```

**GET /jobs/{job_id}** — Poll status
```python
# Response
{
  "job_id": "abc12def34",
  "state": "running",     # queued | running | succeeded | failed | cancelled
  "elapsed_s": 15.2,
  "error": null,
  "log_tail": "GPU memory: 8GB, routing layer 1/2...",
  "stage": "routing"      # Optional: what stage failed (if state=failed)
}
```

**GET /jobs/{job_id}/result** — Download routed .kicad_pcb
```python
# Response: application/octet-stream
<binary .kicad_pcb file with routed tracks>
```

**GET /health** — Service readiness
```python
{
  "ok": true,
  "gpu": "NVIDIA A100",
  "queue": {"waiting": 0}
}
```

### Key Difference from FreeRouting

Unlike FreeRouting (DSN→SES model), OrthoRoute operates on `.kicad_pcb` directly:
- No DSN export needed
- Requires `.kicad_pcb` to already have nets (from `sync_netlist`)
- Returns a full new `.kicad_pcb` binary (not a SES text file)
- Our code: read `.kicad_pcb`, POST to service, GET result, write it back with a `.bak` backup

---

## 5. Net Classes + Design Rules

Routing decisions depend on per-net parameters. In KiCad's DSN/SES model:

### Per-Net Class Rules (in DSN structure block)
```lisp
(class <class_name>
  (clearance <distance_um>)       # Min spacing from other nets
  (trace_width <width_um>)        # Preferred trace width
  (via_width <outer_um> <drill_um>)
)
```

### Our Data Model Perspective

From `/hw_agent/domain/subsystem.py` and related templates:
- `Subsystem` has `requirements` dict + `actuals` dict
- Each subsystem carries:
  - `current_continuous_max_a` — drives trace width (wider for power)
  - `speed_hz` — drives controlled impedance (for high-speed digital)
  - `interface.type` — "power", "signal", "data"

**Mapping missing**: The schematic layer knows `current_continuous_max_a` and `speed_hz`, but the router only sees the DSN. There is NO bridge from subsystem interface properties → net class rules in DSN.

### Typical Numeric Values (4-layer board)

```
POWER class (5A continuous):
  trace_width: 500 um (0.5 mm)    # From IPC-2221: ~30 mils for 5A
  clearance: 150 um
  via: 800 um outer, 400 um drill

SIGNAL class (low-speed digital):
  trace_width: 250 um (0.25 mm)   # Default KiCad
  clearance: 200 um
  via: 800 um outer, 400 um drill

HIGH_SPEED class (USB, differential):
  trace_width: 150 um (0.15 mm)   # For 90 ohm differential
  clearance: 100 um
  via: 600 um outer, 300 um drill
  # plus impedance control (not in standard DSN, KiCad extension)
```

---

## 6. Current Pydantic Contract Validation

### What We Carry → Router

From `/hw_agent/core/subsystem.py` and templates:

**Per-subsystem (already in our contract)**:
```python
# Requirements (engineer input)
iout: float              # A - output current
vin: float               # V - input voltage
vout: float              # V - output voltage
speed_hz: Optional[float] # Hz - I/O frequency (some components)
interface: str           # "i2c", "spi", "power", "signal", ...

# Actuals (datasheet extraction)
current_continuous_max_a: float
theta_ja: float          # Thermal resistance (°C/W)
vdd_min, vdd_max: float
...
```

**What DOESN'T flow to the router**:
- No trace width mapping from `current_continuous_max_a`
- No impedance class from `speed_hz`
- No net class assignment per subsystem/interface

### Where the Gap Is

1. **PCB spec input** (before routing): No schema for design rules
   - Layer stackup (copper weight, spacing)
   - Min trace width / clearance per class
   - Via size / copper requirements

2. **Subsystem → net class mapping**: Schematic knows interfaces have current/speed; PCB doesn't see this context

3. **DSN generation**: `pcb_writer.run_dsn_export()` (KiCad's built-in) generates generic rules; no way to inject our custom per-net-class parameters

### Recommendation

The Pydantic contract **does NOT need to change** for basic routing to work. FreeRouting and OrthoRoute both:
- Accept the DSN as-is (with KiCad's default rules)
- Route successfully without per-net customization
- Rely on the human PCB designer to hand-tune rules in KiCad's GUI if finer control is needed

**However**, for **future automation** (e.g., power distribution optimization):
- Add a `DesignRules` model to the board spec:
  ```python
  class NetClass(BaseModel):
      name: str  # "POWER", "SIGNAL", "HIGH_SPEED"
      trace_width_mm: float
      clearance_mm: float
      via_outer_mm: float
      via_drill_mm: float
  ```
- Map subsystem interfaces → net classes during schematic synthesis
- Inject into DSN before submitting to router

---

## 7. Payload Examples Summary

| Item | Format | Size | Source |
|------|--------|------|--------|
| DSN (small board) | S-expression text | 50–200 KB | KiCad pcbnew export (IPC action) |
| SES (routed output) | S-expression text | 20–100 KB | FreeRouting stdout (JAR execution) |
| FreeRouting API | JSON + HTTP | <1 MB | hw-router-service FastAPI |
| OrthoRoute API | Multipart binary + JSON | kicad_pcb size | hw-orthoroute-service |

---

## 8. Conclusion

### Contract Completeness: ✓ SUFFICIENT for basic routing

Our current Pydantic schemas carry:
- ✓ Subsystem interface type (power/signal/data)
- ✓ Current and speed parameters
- ✓ Component placement via Subsystem model
- ✓ HTTP payloads match wire format (FreeRouting: POST DSN text, GET SES; OrthoRoute: POST .kicad_pcb binary)

### What's Missing: Design Rule Injection (not blocking)

- ✗ No reverse mapping: subsystem current/speed → net class trace width/clearance
- ✗ No per-net-class rule schema in our design input

**Action**: Leave as-is for MVP. When custom routing rules are needed, add `DesignRules` to the board spec and implement the subsystem→net-class bridge in the DSN export stage.

### Files to Review

- `/mcp_server/router/server.py` — MCP tool interface + dispatch
- `/mcp_server/router/engines.py` — FreeRouting and OrthoRoute engine wrappers
- `/hw-router-service/src/hw_router_service/server.py` — API contract (JobRequest, JobStatus)
- `/hw_agent/artifacts/schematics/pcb_writer.py` — DSN/SES import/export
- `/hw_agent/artifacts/schematics/pcb_ipc.py` — KiCad IPC plumbing
- `/hw_agent/core/freerouting.py` — Legacy local routing (retained for backward compat, not used by router-mcp)

---

**Date**: 2026-05-24
**Investigation Focus**: Real payload validation for router-mcp Pydantic contract
