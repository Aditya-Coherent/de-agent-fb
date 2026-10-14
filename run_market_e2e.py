"""End to end: copy a human market's structure, generate the market from
evidence, build the ME, and evaluate it against the human pair.

    python run_market_e2e.py --market "Global High Energy Laser Market" \
        --human-data "src/newinfo/Data file-Global High Energy Laser Market.xlsx" \
        --human-me "src/newinfo/ME - Global High Energy Laser Market.xlsx" --out output/hel --tag v2 --narrate

Steps (the human files supply LABELS AND LAYOUT ONLY -- no number is copied):
    1. structure agent   spec + blank Data / ME templates
    2. generation        Input Sheet from evidence (generate_synthetic_input --skeleton)
    3. Stage B           Input Sheet -> ME, written into the blank human ME (run_pipeline --template)
    4. data writer       the generated Data file re-laid into the human Data structure
    5. evaluator agent   decomposition + grades + (optional) LLM diagnosis
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.generation import data_writer, evaluator_agent as ev, structure_agent as sa  # noqa: E402


def run(cmd: list[str]) -> None:
    print("$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market", required=True)
    p.add_argument("--human-data", required=True)
    p.add_argument("--human-me", required=True)
    p.add_argument("--out", default="output/e2e")
    p.add_argument("--tag", default="run")
    p.add_argument("--skip-generation", action="store_true", help="reuse the existing generated Input Sheet")
    p.add_argument("--gen-input", help="use this generated Input Sheet (implies --skip-generation)")
    p.add_argument("--narrate", action="store_true")
    p.add_argument("--input-only", action="store_true", help="stop at the Data file (Input Sheet); no ME")
    p.add_argument("--regulate", action="store_true", help="assumptor regulation with real-world drivers")
    p.add_argument("--gen-state", help="generation state JSON (scope artifacts) when reusing --gen-input")
    p.add_argument("--events", action="store_true", help="adjust CAGRs for real-world events found in dated news")
    p.add_argument("--usecases", action="store_true",
                   help="build the market bottom-up: industry -> use case -> country driver -> units -> price -> value")
    args = p.parse_args()
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)

    spec = sa.extract(args.human_data, args.human_me, args.market)
    sa.save(spec, out / "structure_spec.json")
    sa.blank_template(args.human_me, out / "ME_template_blank.xlsx")
    data_writer.blank_data_template(args.human_data, out / "Data_template_blank.xlsx")
    print(f"[1/5] structure: {len(spec['geographies'])} countries, {len(spec['dimensions'])} dimensions, "
          f"volume '{spec['me_layout']['volume_unit']}'; checks "
          + ", ".join(f"{c['check']}={'ok' if c['ok'] else 'FAIL'}" for c in spec["checks"]))

    gen_input = (Path(args.gen_input) if args.gen_input else
                 ROOT / "agentGeneratedInputSheet" / f"Data File - {args.market} ({args.tag}).xlsx")
    if not (args.skip_generation or args.gen_input):
        run([sys.executable, "generate_synthetic_input.py", "--market", args.market, "--skeleton", args.human_data,
             "--output", str(gen_input), "--save-state", str(out / f"gen_state_{args.tag}.json")])
    print(f"[2/5] generated Input Sheet: {gen_input}")

    # Stage B reads the Data file in the HUMAN layout (parents followed by their own children):
    # fed the generator's own layout it attaches children to the wrong parents (seen: v5).
    gen_data = out / f"Data File - {args.market} ({args.tag}, human structure).xlsx"
    stats = data_writer.fill(spec, out / "Data_template_blank.xlsx", gen_input, None, gen_data)
    print(f"[3/5] Data file in human structure: {gen_data} {stats}")

    llm = None
    if args.regulate or args.narrate or args.events or args.usecases:
        from demand_country_2a_agent import deepseek_config
        from me_engine.generation.llm import GenLLM
        llm = GenLLM(deepseek_config("deepseek-chat"), cache_path=ROOT / "output/.gen_cache.db")
    regulation = None
    if args.regulate:
        from me_engine.generation import assumptor_regulator as ar
        from me_engine.generation.skeleton import load_skeleton_from_human_sheet
        state_path = Path(args.gen_state) if args.gen_state else out / f"gen_state_{args.tag}.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        nesting = load_skeleton_from_human_sheet(Path(args.human_data), args.market).nesting
        regulation = ar.regulate(llm, spec, nesting, state.get("scope_reconciliation"), out / "assumptor",
                                 generated=ev.read_data_file(gen_data, spec))
        wstats = ar.write(spec, gen_data, regulation)
        cons = ar.consistency(llm, spec, nesting, regulation, ev.read_data_file(gen_data, spec))
        wstats["consistency_cells"] = ar.write_consistency(spec, gen_data, cons)
        regulation["consistency"] = {"notes": cons["notes"], "llm": cons["llm"]}
        (out / f"regulation_{args.tag}.json").write_text(json.dumps(regulation, indent=2, default=str), encoding="utf-8")
        print(f"[4/5] assumptor regulation: global ${regulation['global_value']:,.0f}M {wstats}")

    usecase = None
    if args.usecases:
        from me_engine.generation import assumptor_regulator as ar2
        from me_engine.generation import usecase_builder as ub_mod
        from me_engine.generation.skeleton import load_skeleton_from_human_sheet as _sk2
        from me_engine.generation.web_evidence import gather as _gather
        state_path = Path(args.gen_state) if args.gen_state else out / f"gen_state_{args.tag}.json"
        gstate = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        comps = (((gstate.get("scope_reconciliation") or {}).get("cache") or {}).get("bottom_up") or {}).get("components") or []
        published = {c["segment"]: [{"publisher": f.get("publisher"), "usd_mn": round(f.get("adjusted_mn") or 0, 1),
                                     "independent": bool(f.get("independent"))} for f in c.get("figures") or []]
                     for c in comps}
        nest = _sk2(Path(args.human_data), args.market).nesting
        usecase = ub_mod.build(llm, spec, nest, args.market, out / "assumptor",
                               lambda qs: _gather(qs, per_query=5, cap=10), published)
        ustats = ub_mod.write(spec, gen_data, usecase)
        # every other dimension bottom-up: what each use case buys, weighted by the country's use-case values
        from me_engine.generation import usecase_mix
        usecase["mix"] = usecase_mix.build(llm, usecase, nest, args.market)
        ustats["mix_cells"] = usecase_mix.write(spec, gen_data, usecase["mix"])
        (out / f"usecases_{args.tag}.json").write_text(json.dumps(usecase, indent=2, default=str), encoding="utf-8")
        print(f"[4/5] use-case build: {len(usecase['use_cases'])} use cases, global ${usecase['global_value']:,.0f}M {ustats}")

    event_scan, event_applied = None, None
    if args.events:
        from me_engine.generation import events_agent
        from me_engine.generation.skeleton import load_skeleton_from_human_sheet as _sk
        sk = _sk(Path(args.human_data), args.market)
        event_scan = events_agent.scan(llm, args.market, {d: (tr.get("__root__") or [])[:8] for d, tr in sk.nesting.items()},
                                       spec["regions"], spec["geographies"])
        event_applied = events_agent.apply(gen_data, event_scan, spec["regions"])
        (out / f"events_{args.tag}.json").write_text(json.dumps({"scan": event_scan, "applied": event_applied},
                                                                indent=2, default=str), encoding="utf-8")
        print(f"[4/5] events: {len(event_scan['events'])} verified events; global CAGR "
              f"{event_applied['global_cagr_baseline']} -> {event_applied['global_cagr_adjusted']}")

    gen_me = None
    if not args.input_only:
        gen_me = ROOT / "agentGeneratedMEWorkbook" / f"ME - {args.market} ({args.tag}).xlsx"
        run([sys.executable, "run_pipeline.py", "--input", str(gen_data), "--output", str(gen_me),
             "--template", str(out / "ME_template_blank.xlsx"), "--save-report", str(out / f"stageB_{args.tag}.json")])
        stats = data_writer.fill(spec, out / "Data_template_blank.xlsx", gen_input, gen_me, gen_data)  # + CAGR block
        if regulation:
            ar.write(spec, gen_data, regulation)
        print(f"[4/5] ME: {gen_me}; Data file CAGR block filled from it {stats}")

    rep = ev.evaluate(spec, Path(args.human_data), None if args.input_only else Path(args.human_me), gen_data, gen_me)
    narrative = ev.narrate(llm, rep) if args.narrate else ""
    (out / f"evaluation_{args.tag}.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    md = (ev.write_input_markdown(rep, out / f"evaluation_{args.tag}.md", regulation, narrative) if args.input_only
          else ev.write_markdown(rep, out / f"evaluation_{args.tag}.md", narrative))
    if event_scan:
        from me_engine.generation import events_agent
        with open(md, "a", encoding="utf-8") as fh:
            fh.write("\n\n" + events_agent.markdown(event_scan, event_applied))
    print(f"[5/5] {rep['verdict']['summary']}\n      {json.dumps(rep['verdict']['grades'])}\n      report: {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
