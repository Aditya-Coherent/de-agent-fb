"""Live progress chart for a run (`src/newinfo/system.md` §13).

Regenerates `progress.mermaid` from the design flowchart
(`src/newinfo/flowchart.mermaid`): every node gets a status class
(pending / running / done / flagged / failed / skipped) and, where known, a
counter such as "Evidence 13/13 countries". The build-status legend of the
design chart is replaced by a run-status legend.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

FLOWCHART = Path(__file__).resolve().parents[2] / "newinfo" / "flowchart.mermaid"
STATUSES = ("pending", "running", "done", "flagged", "failed", "skipped")
_CLASSDEFS = [
    "    classDef pending fill:#f5f5f5,stroke:#9e9e9e,color:#555",
    "    classDef running fill:#fff3cd,stroke:#b8860b,color:#4a3800",
    "    classDef done fill:#d4edda,stroke:#2e7d32,color:#1b3a1f",
    "    classDef flagged fill:#ffe0b2,stroke:#e65100,color:#4e2600",
    "    classDef failed fill:#f8d7da,stroke:#b71c1c,color:#5c0b0b",
    "    classDef skipped fill:#eeeeee,stroke:#777,color:#333,stroke-dasharray:5 5",
]
_NODE_ID = re.compile(r"\b([A-Z][A-Z0-9_]+)\b")
_LABEL_TEXT = re.compile(r'"[^"]*"|\|[^|]*\|')
_NOT_NODES = {"TD", "DATA", "LEGEND"}


def _add_counter(text: str, node: str, counter: str) -> str:
    """Append the counter inside the first quoted label that defines `node`."""
    pattern = r"(\b" + re.escape(node) + r'\s*[\[\(\{]+"[^"]*)(")'
    return re.sub(pattern, lambda m: m.group(1) + "<br/><b>" + counter + "</b>" + m.group(2), text, count=1)


class Progress:
    def __init__(self, path: Path, template: Path = FLOWCHART) -> None:
        self.path = path
        self.template = template
        self.status: dict[str, str] = {}
        self.counters: dict[str, str] = {}

    def set(self, node: str, status: str, counter: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(status)
        self.status[node] = status
        if counter:
            self.counters[node] = counter
        self.write()

    def render(self) -> str:
        if not self.template.exists():
            return ""
        out: list[str] = []
        nodes: list[str] = []
        in_legend = False
        for line in self.template.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("subgraph LEGEND"):
                in_legend = True
                continue
            if in_legend:
                in_legend = s != "end"
                continue
            if s.startswith("classDef") or s.startswith("class "):
                continue
            if not s.startswith(("flowchart", "subgraph", "end")):
                for ref in _NODE_ID.findall(_LABEL_TEXT.sub("", line)):
                    if ref not in _NOT_NODES and ref not in nodes:
                        nodes.append(ref)
            out.append(line)
        text = "\n".join(out)
        for node, counter in self.counters.items():
            text = _add_counter(text, node, counter)
        out = text.split("\n")
        out += ["", '    subgraph LEGEND["Run status"]']
        out += [f'        S_{st.upper()}["{st}"]' for st in STATUSES]
        out += ["    end", ""] + _CLASSDEFS
        by_status: dict[str, list[str]] = {}
        for node in nodes:
            by_status.setdefault(self.status.get(node, "pending"), []).append(node)
        for st in STATUSES:
            out.append(f"    class {','.join(by_status.get(st, []) + [f'S_{st.upper()}'])} {st}")
        return "\n".join(out) + "\n"

    def write(self) -> None:
        text = self.render()
        if text:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(text, encoding="utf-8")

    def table(self) -> list[dict[str, Any]]:
        return [{"node": n, "status": s, "counter": self.counters.get(n, "")} for n, s in self.status.items()]
