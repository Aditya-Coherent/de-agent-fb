// Completeness audit for the demo app: renders every cell's document and
// reports, per cell type, how many lack a recorded evidence trail or reasoning.
//   node demo/audit_docs.js demo/<app>/index.html [--show "Data!C5"]
const fs = require("fs");
const file = process.argv[2];
const show = process.argv.indexOf("--show") > 0 ? process.argv[process.argv.indexOf("--show") + 1] : null;
const html = fs.readFileSync(file, "utf8");
let js = html.slice(html.indexOf("<script>") + 8, html.lastIndexOf("</script>"));
const els = {};
const mk = id => els[id] || (els[id] = {innerHTML: "", textContent: "", classList: {add() {}, remove() {}, toggle() {}},
  addEventListener() {}, dataset: {}, click() {}, scrollIntoView() {}});
global.document = {getElementById: mk, querySelectorAll: () => [], querySelector: () => null};
global.window = {addEventListener() {}};
global.location = {hash: ""};
js += "\n;globalThis.__t = {DATA, openDoc, setSheet: s => { current = s; }};";
eval(js);
const T = globalThis.__t;
const numericKinds = new Set(["share", "geo_share", "geo_value", "geo_cagr", "mv_value", "mv_share", "mv_cagr", "asp", "asp_share"]);
const stats = {};
let errors = 0;
const colName = n => { let s = ""; while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); } return s; };
for (const [s, sh] of Object.entries(T.DATA.sheets)) {
  T.setSheet(s);
  for (const cell of sh.cells) {
    if (!numericKinds.has(cell.k) || typeof cell.v !== "number") continue;
    let body = "";
    try { T.openDoc(cell); body = els["doc-body"].innerHTML; } catch (e) { errors++; continue; }
    const st = stats[cell.k] || (stats[cell.k] = {cells: 0, with_evidence: 0, with_reasoning: 0, undocumented: 0});
    st.cells++;
    if (/<b>Evidence\.<\/b>|Bottom-up calculation|What each use case buys|is the sum of its use cases|Bottom-up build|Evidence it read|Evidence it cited/.test(body)) st.with_evidence++;
    if (/Its reasoning|The agent's demand logic|Why \(agent\)|is the sum of its use cases|Bottom-up build/.test(body)) st.with_reasoning++;
    if (/did not record|No reasoning was returned/.test(body)) st.undocumented++;
    if (process.argv.includes("--list-noreason") && typeof cell.v === "number" && !/Its reasoning|The agent's demand logic|Why \(agent\)|is the sum of its use cases|Bottom-up build/.test(body)) console.log(`${s}!${colName(cell.c)}${cell.r}`, cell.dim, (cell.path||[]).join(' > '), cell.geo);
    if (show && show === `${s}!${colName(cell.c)}${cell.r}`) {
      console.log(body.replace(/<\/(li|p|h3|tr|div)>/g, "\n").replace(/<[^>]+>/g, " ").replace(/[ \t]+/g, " ").replace(/\n\s*\n+/g, "\n"));
    }
  }
}
console.log("render errors:", errors);
console.table(stats);
