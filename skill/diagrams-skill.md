---
name: diagrams
purpose: Draw a live Mermaid diagram inside the reply when structure matters more than prose.
---

## Use when

- the reply explains a pipeline, architecture, flow or state machine the user needs to SEE
- the user explicitly asks to draw, map out, visualize or diagram something
- a branching decision tree (if/else paths) is the core subject
- you reference 3+ components that act on each other in a sequence or graph

## Do not use when

- simple factual or conversational answers (greetings, jokes, facts)
- the flow is linear with 2-3 steps — bullet words say it faster
- the user asked to keep it short or spoken-only

## How

Call the `render_diagram` tool with valid Mermaid source (flowchart / sequenceDiagram / stateDiagram / classDiagram / erDiagram). The tool renders the diagram on the Nebius token sandbox and returns SVG. After a successful call, emit a `<pre><code class="language-mermaid">...</code></pre>` block with the validated source so the miniapp renders it live via pele, introduced by one line of "why this diagram". Keep to core syntax: 6-14 named nodes, labels under ~4 words, short edge labels, no styling directives, no subgraphs unless essential. One diagram per reply; never more, and never a diagram AND a duplicate yaml/codeblock of the same thing. NOT a ``` fence, NOT an invented <mermaid> tag — call the `render_diagram` tool instead.

## Example

The turn lifecycle has three phases.
<pre><code class="language-mermaid">flowchart LR
  A([Voice starts]) --> B{EOU verdict}
  B -- real turn --> C([Agent answers])
  B -- "mm-hmm" --> D([Reply resumes])</code></pre>
