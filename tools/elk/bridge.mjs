#!/usr/bin/env node
// ELK layout bridge: read an ELK graph JSON from stdin, run layout, write
// the laid-out graph (node x/y/width/height + edge routing sections) to
// stdout. Used by hw_toolkit.kicad.layout_elk via subprocess.
import ELK from "elkjs/lib/elk.bundled.js";

const elk = new ELK();

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", async () => {
  try {
    const graph = JSON.parse(raw);
    const out = await elk.layout(graph);
    process.stdout.write(JSON.stringify(out));
  } catch (e) {
    process.stderr.write(String(e && e.stack ? e.stack : e));
    process.exit(1);
  }
});
