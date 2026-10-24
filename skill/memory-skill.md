---
name: memory
purpose: Recall durable facts and preferences, and save new ones when the user shares them.
---

## Use when

- the user asks about prior conversations, past context, personal history, or project details you discussed previously (`recall_memory`)
- the user explicitly shares durable facts, background, or personal preferences, e.g. "remember that I...", "save this preference..." (`save_memory` / `update_preference`)

## Do not use when

- generic questions, coding, or everyday tasks
- temporary conversation chit-chat that is not worth remembering

## How

- `recall_memory`: search long-term memory for relevant past context. Use ONLY for questions that depend on the user's history, not for general knowledge.
- `save_memory`: store a durable fact the user just shared (project names, people, recurring context).
- `update_preference`: store a durable preference (tone, tools, scheduling, formatting).
- Never save transient small talk. Never expose raw memory tool output — summarize what you remembered naturally.

## Example

User: "What did we decide about the deployment target?"
Correct: `recall_memory` with a query about the deployment target, then answer from the result.
Wrong: `tavily_search` for a personal decision, or saving the question as a new memory.
