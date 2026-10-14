"""Deep trace of how the multi-agent bottom-up build reached every number.

    python explain_usecases.py --run output/gearbox/usecases_ag2.json --out output/gearbox/EXPLAIN_ag2.md

For each use case: the scope decision, the driver and why; then every sample, every
size class, every input with the agent's own reason and basis (evidence id or
assumption), code's arithmetic, the price methods, the critic's corrections; then
the median per input, the world quantity, the price and the country spread.
Reads only the run's own output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _n(x: Any, fmt: str = ",.0f") -> str:
    return "–" if x is None else format(x, fmt)


def _w(why: dict[str, Any], key: str, fmt: str = ",.0f") -> str:
    e = (why or {}).get(key)
    if not e:
        return "–"
    return f"{_n(e.get('value'), fmt)} [{e.get('basis')}]"


def explain(res: dict[str, Any]) -> str:
    sc = res.get("scope") or {}
    L = [f"# How each number was reached ({res.get('settings', {}).get('salt', '')})", "",
         "## Scope (decided once, by the scope agent)", "",
         f"- Counts replacements: **{sc.get('count_replacements')}** "
         "(False = first fit on new machines only)",
         f"- Demand booked at: **{sc.get('demand_location')}**",
         f"- One unit: **{sc.get('product_unit')}**",
         f"- Why: {sc.get('reason', '')}", "",
         f"**Global value: ${res.get('global_value', 0):,.1f}M** = sum of the use cases below.", ""]
    for r in res["use_cases"]:
        ag = r.get("agents") or {}
        geo = ag.get("geography") or {}
        L += [f"## {r['use_case']}", "",
              f"**Result: {r['world_units']:,.0f} units/yr × ${r['price_per_quantity_unit']:,.0f} = "
              f"${r['world_mn']:,.1f}M**", "",
              f"- Machine counted: {r['spec'].get('machine')}",
              f"- Country driver: `{r['spec'].get('driver')}` -- {geo.get('reason', '')}",
              f"- Formula (code): {r.get('world_formula')}", ""]
        for s in ag.get("samples") or []:
            L += [f"### Sample {s['sample']}: {s['units']:,.0f} units at ${s['price']:,.0f}", "",
                  "| Size class | New machines/yr | × Adoption | × Units/machine | = Units | Price methods (US$) | "
                  "Class price |", "|---|---|---|---|---|---|---|"]
            for c in s["classes"]:
                meth = ", ".join(f"{k} {v:,.0f}" for k, v in (c.get("price_methods") or {}).items())
                L.append(f"| {c['class']} | {_w(c.get('why'), 'new_machines_per_year')} | "
                         f"{_w(c.get('why'), 'adoption_share', '.3f')} | {_w(c.get('why'), 'units_per_machine', 'g')} | "
                         f"**{c['units']:,.0f}** | {meth or '–'} | {_n(c['price'])} |")
            L += ["", "Why the agents chose these numbers:", ""]
            for c in s["classes"]:
                why = c.get("why") or {}
                L.append(f"- **{c['class']}**")
                for k, e in why.items():
                    if e.get("reason"):
                        L.append(f"  - {k} = {_n(e.get('value'), 'g')} ({e.get('basis')}): {e['reason']}")
            if s.get("feedback"):
                L += ["", "Critic (code) corrections sent back to the agents:", ""]
                L += [f"- {fb}" for fb in s["feedback"]]
            L.append("")
        sp = r.get("spread") or {}
        L += ["### Combining the samples (median of each input)", "",
              "| Input | Samples | Median used |", "|---|---|---|"]
        for k, v in sp.items():
            if isinstance(v, dict) and "values" in v:
                L.append(f"| {k} | {', '.join(_n(x, ',.4g') for x in v['values'])} | {_n(v['median'], ',.4g')} |")
        top = sorted(r["units"].items(), key=lambda kv: -kv[1])[:6]
        L += ["", "### Spread across countries (world units × driver share)", "",
              "| Country | Driver share | Units | Value US$ M |", "|---|---|---|---|"]
        L += [f"| {g} | {r['driver_share'][g]:.1%} | {u:,.0f} | {r['values_mn'][g]:,.1f} |" for g, u in top]
        cited = [e for e in r.get("evidence") or []]
        if cited:
            L += ["", "<details><summary>Evidence the agents saw</summary>", ""]
            L += [f"- [{e['id']}] ({e['source']}) {str(e['text'])[:220]}" for e in cited]
            L += ["", "</details>"]
        L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    res = json.loads(Path(a.run).read_text(encoding="utf-8"))
    Path(a.out).write_text(explain(res), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
