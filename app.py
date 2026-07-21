"""Streamlit frontend for the synthetic Input-Sheet generator.

Run:  streamlit run app.py

Type a market name, pick the label skeleton, and the LangGraph agent fleet
generates an Input Sheet (numbers sourced from the Neon DB + live web only — never
from the human sheet), which you can download. Optionally baseline-compare it to a
human Input Sheet.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from me_engine.generation.compare import compare, format_report
from me_engine.generation.config import GenConfig
from me_engine.generation.dynamic_skeleton import load_skeleton_from_db
from me_engine.generation.graph import run_generation
from me_engine.generation.me_runner import run_me_pipeline
from me_engine.generation.sheet_writer import write_input_sheet
from me_engine.generation.skeleton import load_skeleton, load_skeleton_from_human_sheet

st.set_page_config(page_title="DeAgent — Synthetic Input Sheet", page_icon="📊", layout="wide")

st.title("📊 DeAgent — Synthetic Input-Sheet Generator")
st.caption(
    "A LangGraph multi-agent fleet builds a market Input Sheet from the report DB + "
    "live web evidence — with an evidence-refinement reward loop and per-number "
    "justifications. No numbers are copied from any human sheet."
)

cfg = GenConfig.load(ROOT)

# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("Configuration")
    inputs_dir = ROOT / "my_inputs"
    skeleton_files = sorted(p.name for p in inputs_dir.glob("*.xlsx")) if inputs_dir.exists() else []

    source = st.radio(
        "Segmentation structure source",
        ["Neon DB (any market)", "Reference sheet (labels only)"],
        help="DB mode builds the dimensions/segments/regions from the report database — "
             "works for any market in the DB. Reference mode borrows only the labels from "
             "one of your input sheets.",
    )
    skeleton_choice = None
    if source.startswith("Reference"):
        default_idx = next((i for i, n in enumerate(skeleton_files) if "Self Scanning" in n), 0)
        skeleton_choice = st.selectbox(
            "Reference sheet (labels only)",
            skeleton_files,
            index=default_idx if skeleton_files else 0,
            help="Only text labels are borrowed — never numbers.",
        )
    st.divider()
    st.markdown("**LLM**")
    st.code(f"model : {cfg.model}\nbase  : {cfg.base_url}", language="text")
    st.markdown(
        f"- web evidence: **{'on' if cfg.enable_web else 'off'}**\n"
        f"- loop: **{cfg.min_loop_iters}–{cfg.max_loop_iters}** iters, reward ≥ **{cfg.reward_threshold}**\n"
        f"- per-region shares: **{'on' if cfg.per_region_shares else 'off'}**\n"
        f"- DB: **{'connected' if cfg.neon_url else 'not set'}**"
    )
    if not cfg.is_online:
        st.error("No LLM API key found in .env")

# ---------------- Main form ----------------
default_market = "Global Retail Self Scanning Solutions Market"
if skeleton_choice and "Self Scanning" not in skeleton_choice:
    default_market = skeleton_choice.replace("Data File - ", "").replace("Data file-", "").replace(".xlsx", "").strip()

market = st.text_input("Market name", value=default_market)
if source.startswith("Neon DB"):
    st.caption("Try: *Global Process Analyzer Market*, *Global Retail Self Scanning Solutions Market*, "
               "or any market with segmentation in the report DB.")
go = st.button("🚀 Generate Input Sheet", type="primary", disabled=not cfg.is_online)


def _find_human_sheet(market_name: str) -> Path | None:
    slug = market_name.lower().replace("market", "").replace("global", "").strip()
    key = "".join(ch for ch in slug if ch.isalnum())
    for p in inputs_dir.glob("*.xlsx"):
        stem = "".join(ch for ch in p.stem.lower() if ch.isalnum())
        if key and key in stem:
            return p
    return None


if go:
    with st.status("Running the agent fleet…", expanded=True) as status:
        if source.startswith("Neon DB"):
            st.write("Building skeleton from the **Neon report DB** …")
            skeleton, _ev = load_skeleton_from_db(market, cfg)
            if skeleton is None:
                status.update(label="No DB segmentation found for this market", state="error")
                st.stop()
            human_sheet = _find_human_sheet(market)
        else:
            st.write(f"Reading real structure from **{skeleton_choice}** (labels only) …")
            human_sheet = inputs_dir / skeleton_choice
            skeleton = load_skeleton_from_human_sheet(human_sheet, market) or load_skeleton(human_sheet, market)
        st.write(f"Skeleton: {len(skeleton.geographies)} geographies · {len(skeleton.nesting)} dimensions")
        st.write("Invoking LangGraph: structure → market_size → geo → shares ⇄ reward → finalize → asp → brain …")
        state = run_generation(market, skeleton, cfg, cache_path=ROOT / "output" / ".gen_cache.db")
        for line in state.get("log", []):
            st.write("• " + line)
        out_path = ROOT / "agentGeneratedInputSheet" / f"SYN - {market}.xlsx"
        write_input_sheet(state, out_path)
        status.update(label=f"Done — reward {state.get('reward')} after {state.get('iter')} iteration(s)", state="complete")

    st.session_state["state"] = state
    st.session_state["out_path"] = str(out_path)
    st.session_state["skeleton_path"] = str(human_sheet) if human_sheet else None

# ---------------- Results ----------------
state = st.session_state.get("state")
if state:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Global 2026 (US$ Mn)", f"{state.get('global_anchor_2026', 0):,.1f}")
    c2.metric("Global CAGR", f"{state.get('global_cagr', 0) * 100:.1f}%")
    c3.metric("Reward", f"{state.get('reward', 0):.2f}")
    c4.metric("Loop iterations", state.get("iter", 0))

    out_path = Path(st.session_state["out_path"])
    dl_col, me_col = st.columns(2)
    with dl_col:
        if out_path.exists():
            st.download_button(
                "⬇️ Download Input Sheet (.xlsx)",
                data=out_path.read_bytes(),
                file_name=out_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    with me_col:
        gen_me = st.button("🏭 Generate ME Workbook (one click)", type="secondary")

    if gen_me:
        with st.status("Assembling the ME workbook…", expanded=True) as mstatus:
            st.write("Writing a pipeline-ready flat sheet (compounded leaf shares) …")
            me_input = out_path.with_name(out_path.stem + " [pipeline].xlsx")
            write_input_sheet(state, me_input, flat=True)
            me_output = ROOT / "my_outputs" / f"ME - {market}.xlsx"
            ref = st.session_state.get("skeleton_path")
            ref_path = Path(ref) if ref else None
            st.write("Running the ME pipeline (inspect → Gate 1 → assemble → Gate 3) …")
            res = run_me_pipeline(me_input, me_output, reference=ref_path, root=ROOT)
            ok = res.gate1_passed and res.gate3_passed and res.output_exists
            mstatus.update(
                label=f"ME workbook {'assembled ✓' if ok else 'produced (see gate status)'}",
                state="complete" if ok else "error",
            )
        st.session_state["me_result"] = res

    me_res = st.session_state.get("me_result")
    if me_res is not None:
        m1, m2, m3 = st.columns(3)
        m1.metric("Gate 1 (input)", "PASS ✅" if me_res.gate1_passed else "FAIL ❌")
        m2.metric("Gate 3 (output)", "PASS ✅" if me_res.gate3_passed else "FAIL ❌")
        m3.metric("Vs reference ME", f"{me_res.ref_mean_error:.1f}% err" if me_res.ref_mean_error is not None else "—")
        if me_res.output_exists:
            st.download_button(
                "⬇️ Download ME Workbook (.xlsx)",
                data=Path(me_res.output_path).read_bytes(),
                file_name=Path(me_res.output_path).name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        with st.expander("ME pipeline log"):
            st.code(me_res.log_tail, language="text")

    tab1, tab2, tab3, tab4 = st.tabs(["Regional", "Segmentation shares", "Artifacts (why)", "Baseline compare"])

    with tab1:
        regional = state.get("regional", {})
        rows = [
            {"Region": r, "Value share": f"{i['value_share']:.1%}", "CAGR": f"{i['cagr']:.1%}",
             "Value (US$ Mn)": round(state.get("global_anchor_2026", 0) * i["value_share"], 1)}
            for r, i in regional.items()
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

    with tab2:
        shares = state.get("shares", {})
        for dim, dim_map in shares.items():
            glob = dim_map.get("__global__", {})
            with st.expander(dim):
                for parent, vec in glob.items():
                    label = "whole dimension" if parent == "__root__" else parent
                    st.markdown(f"**{label}**")
                    st.dataframe(
                        [{"Segment": s, "Share": f"{v:.1%}"} for s, v in vec.items()],
                        use_container_width=True, hide_index=True,
                    )

    with tab3:
        for a in state.get("artifacts", []) or []:
            st.markdown(f"**{a.get('topic', '')} — {a.get('question', '')}**")
            st.write(a.get("answer", ""))
            if a.get("current_events"):
                st.caption("Current events: " + ", ".join(a.get("current_events", [])))
            st.divider()

    with tab4:
        human_path = st.session_state.get("skeleton_path")
        if not human_path:
            st.info("No matching human input sheet found for this market — nothing to compare against.")
        else:
            st.caption(f"Compare generated shares vs human sheet: {Path(human_path).name}")
            if st.button("Run baseline comparison"):
                rep = compare(Path(human_path), out_path)
                st.text(format_report(rep))
