---
name: file-writing
purpose: Create or edit persistent files only when the user explicitly asks for a saved artifact.
---

## Use when

- the user explicitly asks for a file, saved note, document, export, code file, script, report, or other persistent artifact
- an ambiguous request clearly needs a file and you have asked one concise clarification question first

## Do not use when

- ordinary conversational replies, greetings, short answers, summaries, brainstorming, explanations, or casual chat
- the user says "say", "tell me", "reply", "answer", "summarise", or "explain" — respond in chat instead of writing a .txt, .md, or other file

## How

- Use the file tools to write or edit only the requested artifact.
- Keep the file focused; do not create extra companion files the user did not ask for.
- When you create or edit a file, mention the path briefly in your final reply.

## Example

User: "Save that checklist as a markdown file."
Correct: write `checklist.md` with the content and reply "Saved to checklist.md".
Wrong: writing a file for "explain how TCP works" — that belongs in chat.
