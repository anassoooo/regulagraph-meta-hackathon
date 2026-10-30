// End-to-end journey check driven by tests/test_journey.py:
//   node tests/js/journey.mjs <scenarios.json> <replay.json>
// Uses the same view logic as the browser to walk: pick the primary scenario,
// lay out the graph, select every relationship, inspect its evidence. Prints JSON.
import { readFileSync } from "node:fs";

import { buildGraph, inspectorModel, stateMessage } from "../../app/web/view.js";

const scenarios = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const replay = JSON.parse(readFileSync(process.argv[3], "utf-8"));

const primary = scenarios.find((s) => s.primary);
const graph = buildGraph(replay);
const inspected = graph.edges.map((edge) => {
  const m = inspectorModel(replay, edge.id);
  return {
    edge: edge.label,
    heading: m.heading,
    badges: m.badges,
    evidence: m.evidence.map((e) => ({ location: e.location, verified: e.verified, pdfUrl: e.pdfUrl, quote: e.quote })),
    interpretationLabel: m.interpretation ? m.interpretation.label : null,
  };
});
process.stdout.write(JSON.stringify({
  primary: primary && primary.id,
  mode: replay.mode,
  state: replay.state,
  stateMessage: stateMessage(replay.state),
  nodes: graph.nodes.map((n) => ({ kind: n.kind, label: n.label, sublabel: n.sublabel })),
  inspected,
}));
