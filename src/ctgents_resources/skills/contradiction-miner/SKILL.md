# Contradiction Miner

## Overview

Mine contradictions from research literature in a domain. The output is a **contradiction pair list** — each entry names two (or more) papers that make mutually incompatible claims, the evidence each side provides, and a minimal experiment that could resolve the contradiction.

This skill does NOT: rank papers, evaluate paper quality, propose new methods, or write paper drafts. It finds contradictions.

## When to Use

Use when:
- User wants to find research gaps in a domain
- User asks "what are the contradictions in X field?"
- User needs candidate research problems from literature
- User says: 找矛盾, 文献矛盾, 找研究缺口, 这个领域有什么没解决的问题

Do NOT use for:
- Single-paper deep read (use paper-deep-read pattern instead)
- Literature search without contradiction analysis
- Writing or reviewing papers

## Four-Step Pipeline

### Step 1: Exhaustive Search

Multi-angle search to build comprehensive paper pool:

1. Start with broad keyword combinations (3-4 angles): "X Y", "X Z", "X survey", "X 2025 2026"
2. Use scholar depth for academic sources, fast for initial sweep
3. Track papers in a pool list with: title, arxiv ID, venue, year, one-line topic
4. Stop when new searches return mostly already-seen papers.

Target: 15-30 papers for a focused subfield, 30-60 for a broad area.

### Step 2: Structured Deep Read

For each paper, delegate to workers for parallel deep reading. Extract a standardized card:

```text
Paper: [title, arxiv ID, venue, year]
Core claim: [one sentence — what this paper asserts is its main contribution]
Problem solved: [what gap it claims to fill]
Method mechanism: [HOW it works, not just what it's called — trace input→output with specific operations]
Key assumptions: [explicit + implicit premises the method depends on]
Evidence: [datasets, baselines, metrics, key numbers]
Self-acknowledged limitations: [from paper's own discussion/conclusion]
Inferred failure modes: [simulate: if I use this method, where would it break? Think through edge cases, distribution shifts, scale limits, assumption violations. NOT a checklist — reason through the mechanism step by step.]
Representation used: [what format: latent/BEV/occupancy/graph/video/etc.]
WM→planning link: [how world model output feeds into planning — if applicable]
```

Worker output format: one markdown file per paper in `knowledge/paper-deep-read/<slug>.md`.

### Step 3: Cross-Reference Contradiction Mining

Compare all papers pairwise along these axes:

1. **Mutually exclusive assumptions** — Paper A assumes X is important; Paper B shows no benefit from X.
2. **Shared untested premises** — 3+ papers all assume Y, but none verify it.
3. **Same mechanism, different story** — Two papers use essentially the same method but claim different contributions (one says "novel architecture", other says "better training").
4. **Evaluation incomparability** — A and B both claim SOTA on the same task but use different protocols/benchmarks/metrics — their claims can't be compared.
5. **Self-contradiction** — A paper's own results undermine its main claim.
6. **Metric blindness** — Different methods converge to similar scores on a metric, suggesting the metric can't discriminate.
7. **Claimed vs inferred failure modes** — A paper claims robustness under condition C, but mechanistic reasoning (from Step 2 "inferred failure modes") suggests it should break under C. The contradiction is between the paper's claim and the reader's simulated reasoning, not between two papers.

Filtering rules:
- Skip pairs where the contradiction is trivial (e.g., different datasets naturally yield different results).
- Skip pairs where both sides could plausibly both be right (e.g., "method works well on dataset X but poorly on Y" — that's not a contradiction, that's scope).
- Only include contradictions where **at least one side provides explicit evidence** (not just a claim in the introduction).
- Mark confidence: [已核] for direct paper evidence, [推断] for analyst inference.

### Step 4: Produce Contradiction List

Output format for each contradiction:

```markdown
## 矛盾 N：[一句话命名]

**X 方**：[论文] — [具体主张 + 证据]
**Y 方**：[论文] — [具体主张 + 证据]
**为什么互斥**：[不能同时成立的原因，1-2句]
**验证方式**：[最小实验设计，1-2句]
```

End the list with:
- "综述诊断" if a survey paper provided field-level diagnosis
- Summary of the most actionable contradictions (those with clear verification path)

---

## Output

- Contradiction list → `knowledge/domain-maps/contradictions-<domain-slug>.md`
- Individual paper deep reads → `knowledge/paper-deep-read/<paper-slug>.md`

## Reference

- Structured read template: see Step 2 above
- Cross-reference axes: see Step 3 above
- Contradiction output format: see Step 4 above

---

## Notes

- Do not propose new methods or research directions as part of the contradiction list. The list is input for the user's own judgment.
- If a contradiction has no clear verification path, mark it as "needs clarification" rather than omitting it.
- Prioritize contradictions where the conflicting claims come from different research groups (not internal ablations within the same paper).
- Network failures during search: retry with different query formulations; if persistent, note coverage gaps.
