---
name: subagents
purpose: Delegate complex, deep, or long-running work to an autonomous background subagent.
---

## Use when

- the user requests a complex, deep, or long-running task (e.g. comprehensive multi-source web research, in-depth comparative reports, multi-file code architectures, or deep analysis)
- the user explicitly asks to do something in the background or with a subagent

## Do not use when

- the user asks quick, everyday questions, calculations, or routine tasks — answer directly yourself

## How

1. Call `spawn_subagent` with a descriptive `task_name` and complete, detailed `instructions`.
2. Acknowledge to Sir Mario that the subagent is working on it in the background and that he can freely continue chatting about other things.
3. Do not repeat the subagent's work yourself while it runs.

## Example

User: "Research the best on-prem TTS engines in depth and send me a report."
Correct: `spawn_subagent(task_name="TTS engine comparison", instructions="...")`, then acknowledge and let it run.
Wrong: answering with a shallow two-line list, or doing the whole deep research synchronously.
