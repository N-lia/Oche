---
name: voice-notes
purpose: Synthesize and deliver a spoken voice note when (and only when) the user explicitly asks for one.
---

## Use when

- the user EXPLICITLY asks for a voice note, voice message, audio message, or spoken response, e.g. "send me a voice note", "reply in voice", "speak this to me", "say this in audio"

## Do not use when

- ordinary text chats, casual chit-chat, greetings, questions, explanations, code, or research — ordinary conversation MUST ALWAYS be answered directly in chat text

## How

Chatterbox-Turbo paralinguistic tags (official tokenizer support only):

- Physical actions: `[laugh]`, `[chuckle]`, `[gasp]`, `[cough]`, `[sigh]`, `[groan]`, `[sniff]`, `[shush]`, `[clear throat]`
- Emotional states: `[whispering]`, `[angry]`, `[fear]`, `[surprised]`, `[crying]`, `[happy]`, `[sarcastic]`, `[dramatic]`
- Content types: `[narration]`, `[advertisement]`

Tag rules:

1. Place emotion tags at the START of the sentence they affect (e.g., "[happy] Great job!" not "Great job! [happy]").
2. Use action tags INLINE where they naturally occur.
3. DO NOT stack multiple emotion tags (e.g., "[happy][laugh]" is wrong; pick one).
4. Use tags sparingly (1-3 per voice note).
5. Match mood to the dominant emotion: `mood="cheerful"` for [happy]/[laugh]/[chuckle], `mood="angry"` for [angry]/[groan], `mood="normal"` for neutral or [sigh].
6. DO NOT invent tags like [giggle], [roar], [cackle], [sob], [scream], or any other brackets — only the official tags above.

After calling `send_voice_note`, give a brief chat acknowledgment like "Sent you a voice note above! 🎙️" and do NOT repeat the voice note text in your chat reply.

## Example

User: "Send me a voice note congratulating me on finishing the deployment."
Correct: call send_voice_note with text="[happy] Congratulations, Sir Mario! You absolutely crushed that deployment. [chuckle] Everything went live without a hitch.", mood="cheerful", event="task_done", caption="🎉 Deployment celebration".

Wrong: `[excited] Great work [laugh][chuckle]!` — invalid tag plus stacked tags; or calling `send_voice_note` for an ordinary chat turn.
