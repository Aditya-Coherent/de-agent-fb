<!-- code-review-graph MCP tools -->
## Project orientation

Read `PROJECT_STRUCTURE.md` first. It maps every folder, the three market-sizing
approaches (Desk Research, Demand-Side usage-based + India variant, Supply-Side),
the root scripts, the backend/frontend, and `output/`.

## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### Session start check

At the start of every conversation that touches code:

1. Call `list_graph_stats_tool` (or read the SessionStart hook output if
   already present) to get `head_sha` / `built_at_sha` and `age_seconds`.
2. Compare `built_at_sha` against the repo's current HEAD.
   - **Match, reasonable age** → trust the graph, proceed per the workflow below.
   - **Mismatch (stale after commits/merges)** → call `build_or_update_graph_tool`
     before relying on any other graph tool, and say one line to the user
     noting the graph was stale and got rebuilt.
3. Never silently answer a structural/impact question from a graph known to
   be stale.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes_tool` or `query_graph_tool` instead of Grep
- **Understanding impact**: `get_impact_radius_tool` instead of manually tracing imports
- **Code review**: `detect_changes_tool` + `get_review_context_tool` instead of reading entire files
- **Finding relationships**: `query_graph_tool` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview_tool` + `list_communities_tool`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes_tool` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context_tool` | Need source snippets for review — token-efficient |
| `get_impact_radius_tool` | Understanding blast radius of a change |
| `get_affected_flows_tool` | Finding which execution paths are impacted |
| `query_graph_tool` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes_tool` | Finding functions/classes by name or keyword |
| `get_architecture_overview_tool` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes_tool` for code review.
3. Use `get_affected_flows_tool` to understand impact.
4. Use `query_graph_tool` pattern="tests_for" to check coverage.
