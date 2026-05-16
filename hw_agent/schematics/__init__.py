"""Schematic generation, evaluation, and rendering for hw_agent.

Modules:
    circuit_builder — Python DSL (typed nets, @module, interfaces)
    schem_renderer — Pydantic data model
    ksa_writer — Schematic → kicad-sch-api → .kicad_sch (the only writer)
    sch_ops — atomic mutations on .kicad_sch via kicad-sch-api
    json_ops — atomic mutations on .schem.json (legacy edit format)
    validators — pre-flight schema checks
    eval — kicad-cli ERC + classification
    erc_filters / drc_filters — KiBot-style violation classification
    sch_diff — UUID-keyed structural diff for human-edit detection
    render_focus — get_render() with bbox crop
    kicad_paths, kicad_lib — KiCad install + symbol-library lookup
    system_composer — compose per-subsystem .kicad_sch into a hierarchical root
    constraints — parametric constraint engine
    model — Project model (components, rails, pins, I2C buses)
    pcb_writer — PCB pipeline glue (compose_spec, fab export via kicad-cli)
    pcb_backend / pcb_ipc — live PCB edits via kicad-python IPC (pcbnew open)
"""
