# Chatterbox-Turbo Quick Reference

## Configuration Checklist

### ✅ Environment Variables (.env)
```bash
# Correct variable name (not CHATTERBOX_MOODAL_URL)
CHATTERBOX_MODAL_URL=https://your-username--oyi-chatterbox-turbo-chatterboxservice-tts.modal.run

# Required for agent
NEBIUS_API_KEY=your_key_here
TELEGRAM_BOT_TOKEN=your_token_here
My_Telegram_ID=your_telegram_id
```

### ✅ Official Paralinguistic Tags

**Physical Actions** (use inline where they occur):
- `[laugh]` - Full hearty laugh
- `[chuckle]` - Light laugh / amused
- `[gasp]` - Sharp intake of breath
- `[cough]` - Clearing airways
- `[sigh]` - Deep exhale / resignation
- `[groan]` - Complaint / frustration
- `[sniff]` - Nasal sound
- `[shush]` - Quiet gesture
- `[clear throat]` - Polite throat clearing

**Emotional States** (use at START of sentence):
- `[whispering]` - Quiet, secretive tone
- `[happy]` - Cheerful, upbeat
- `[angry]` - Stern, upset
- `[fear]` - Scared, anxious
- `[surprised]` - Shocked, amazed
- `[crying]` - Sad, tearful
- `[sarcastic]` - Ironic, mocking
- `[dramatic]` - Theatrical, exaggerated

**Content Types**:
- `[narration]` - Storytelling mode
- `[advertisement]` - Promotional tone

---

## Usage Patterns

### ✅ CORRECT Examples

#### Celebratory (task completion)
```python
send_voice_note(
    text="[happy] Fantastic work, Sir Mario! The deployment is live and running smoothly. [chuckle] All tests passed on the first try!",
    mood="cheerful",
    event="task_done",
    caption="🎉 Deployment success"
)
```

#### Stern Warning
```python
send_voice_note(
    text="[angry] You pushed to production without running the tests again. [sigh] We've talked about this.",
    mood="angry",
    event="slop",
    caption="⚠️ Code quality reminder"
)
```

#### Casual Chat
```python
send_voice_note(
    text="Sure [chuckle], I can definitely help with that. What file did you want me to look at?",
    mood="normal",
    event="chat",
    caption="Response"
)
```

#### Surprising News
```python
send_voice_note(
    text="[surprised] Wait, the bug fix actually improved performance by 40%? [gasp] That's incredible!",
    mood="normal",
    event="chat"
)
```

---

### ❌ WRONG Examples (DO NOT DO)

#### Invalid Tags
```python
# ❌ 'excited' is not a valid tag
text="[excited] Great job!"

# ✅ Use [happy] instead
text="[happy] Great job!"
```

#### Tags at Wrong Position
```python
# ❌ Emotion tag at end
text="Great job! [happy]"

# ✅ Emotion tag at start
text="[happy] Great job!"
```

#### Stacked Tags
```python
# ❌ Multiple tags together
text="[happy][laugh] Amazing work!"

# ✅ Choose one or space them out
text="[happy] Amazing work! [laugh]"
```

#### Made-Up Tags
```python
# ❌ These are NOT valid tags
text="[giggle] [sob] [roar] [whisper] [scream]"

# ✅ Use official equivalents
text="[chuckle] [crying] [angry] [whispering] [gasp]"
```

#### Wrong Mood Pairing
```python
# ❌ Mismatch: [angry] with cheerful mood
send_voice_note(
    text="[angry] This is wrong!",
    mood="cheerful"  # Wrong!
)

# ✅ Match mood to emotion
send_voice_note(
    text="[angry] This is wrong!",
    mood="angry"  # Correct
)
```

---

## Mood & Event Parameters

### Mood Options
- `"normal"` - Default, calm conversational tone
- `"cheerful"` - Upbeat, happy, celebratory
- `"angry"` - Stern, firm, upset

### Event Options
- `"chat"` - Normal conversation (default)
- `"task_done"` - Task completion (auto-applies cheerful)
- `"slop"` - Warning/error (auto-applies stern tone)

### Recommended Pairings

| Emotion Tag | Mood | Event | Use Case |
|------------|------|-------|----------|
| `[happy]` | cheerful | task_done | Celebrations, achievements |
| `[laugh]`, `[chuckle]` | cheerful | chat | Friendly, amused responses |
| `[angry]`, `[groan]` | angry | slop | Warnings, frustrations |
| `[sigh]` | normal | chat | Mild disappointment |
| `[surprised]`, `[gasp]` | normal | chat | Unexpected news |
| `[sarcastic]` | normal | chat | Playful teasing |

---

## Voice Dials & Configuration Knobs

The generation parameters are tuned per mood via `PRESETS` in `modal_chatterbox.py`:

```python
PRESETS = {
    "normal": {
        "exaggeration": 0.5,       # Baseline natural expressiveness
        "cfg_weight": 0.5,         # Balanced reference adherence & pacing
        "temperature": 0.8,        # Varied and natural inflection
        "repetition_penalty": 1.2, # Standard loop prevention for English
        "min_p": 0.05,             # Safe tail cutoff
        "top_p": 1.0,
    },
    "cheerful": {
        "exaggeration": 0.7,       # Higher emotional intensity
        "cfg_weight": 0.3,         # Lower CFG slows pacing, balancing high exaggeration
        "temperature": 0.8,
        "repetition_penalty": 1.2,
        "min_p": 0.05,
        "top_p": 1.0,
    },
    "angry": {
        "exaggeration": 0.8,       # High sternness
        "cfg_weight": 0.3,         # Deliberate, firm delivery
        "temperature": 0.7,        # Lower temperature for strict, controlled delivery
        "repetition_penalty": 1.3, # Stronger penalty against repeating stern syllables
        "min_p": 0.05,
        "top_p": 1.0,
    },
}
```

### Supported Dials:
- **`exaggeration` (0.25–2.0):** Higher = more emotional/dramatic but speeds up delivery. (>1.5 can create artifacts).
- **`cfg_weight` (0.0–1.0):** Reference audio adherence. Lowering it (~0.2–0.3) slows speech down to balance high exaggeration. 0.0 eliminates cross-language accent bleed.
- **`temperature` (0.05–5.0):** Sampling randomness. 0.6–0.8 recommended. Lower is more monotone/stable.
- **`top_p` (0.1–1.0):** Nucleus sampling. 0.8–1.0 keeps output focused.
- **`min_p` (0.01–0.2):** Filter out improbable phonemes. 0.05–0.10 stops squeaks and glitches.
- **`repetition_penalty` (1.0–3.0):** 1.2 for English. Raise to 1.3–1.5 if you ever hear stuttering or repeated syllables.

---

## Best Practices

1. **Placement Rules**
   - Emotion tags (`[happy]`, `[angry]`) → START of sentence
   - Action tags (`[chuckle]`, `[sigh]`) → INLINE where they occur

2. **Tag Density**
   - Use 1-3 tags per voice note
   - Too many tags = unnatural speech
   - Let the text itself carry most expression

3. **Tag Selection**
   - Pick the strongest emotion that matches the context
   - Don't mix conflicting emotions (`[happy]` and `[angry]`)
   - Use actions to add nuance (`[happy] Great! [chuckle]`)

4. **Mood Matching**
   - Set `mood=` to match the dominant emotion
   - `cheerful` for positive emotions
   - `angry` for negative emotions
   - `normal` for neutral or mixed emotions

5. **Testing**
   - Run `python test_chatterbox_config.py` to validate setup
   - Test samples: `test_cheerful.mp3`, `test_angry.mp3`, `test_normal.mp3`
   - Listen to output to verify naturalness

---

## Common Substitutions

| ❌ Don't Use | ✅ Use Instead |
|-------------|---------------|
| `[giggle]` | `[chuckle]` |
| `[cackle]` | `[chuckle]` or `[laugh]` |
| `[sob]` | `[crying]` |
| `[whisper]` | `[whispering]` |
| `[scream]` | `[surprised]` or `[fear]` |
| `[excited]` | `[happy]` or `[surprised]` |
| `[pause]` | `...` (ellipsis) |
| `[throat clearing]` | `[clear throat]` |

---

## Deployment Commands

### Deploy to Modal
```bash
modal deploy modal_chatterbox.py
```

### Test Locally
```bash
python modal_chatterbox.py demo
```

### Test Remote
```bash
modal run modal_chatterbox.py::demo
```

### Validate Configuration
```bash
python test_chatterbox_config.py
```

---

## Troubleshooting

### "CHATTERBOX_MODAL_URL is not configured"
- Check `.env` file has `CHATTERBOX_MODAL_URL` (not `CHATTERBOX_MOODAL_URL`)
- Ensure URL doesn't have trailing slash
- Format: `https://username--oyi-chatterbox-turbo-chatterboxservice-tts.modal.run`

### "Chatterbox TTS returned status 400"
- Check your `text` parameter is not empty
- Verify payload structure: `{"text": "...", "mood": "...", "event": "...", "format": "mp3"}`

### "Request timed out"
- Modal endpoint might be cold-starting (first request takes 30-60s)
- Increase timeout in `voice.py` if needed (current: 90s)

### "TTS reads tag names literally"
- You used an invalid tag (e.g., `[giggle]`)
- Only use tags from the official list above
- Check spelling: `[clear throat]` not `[throat clearing]`

### "Voice sounds wrong for the mood"
- Ensure `mood=` parameter matches the emotion tags
- Example: `[happy]` text should use `mood="cheerful"`

---

## Integration with OYI Agent

The agent automatically:
1. Only calls `send_voice_note` when you **explicitly ask** for voice
2. Uses the one-shot example in its system prompt
3. Validates tags through `sanitize_and_shape_text()`
4. Strips invalid tags before sending to Chatterbox

**Trigger phrases** (will invoke voice note):
- "send me a voice note"
- "reply in voice"
- "speak to me"
- "say this in audio"

**Won't trigger** (normal text chat):
- Regular questions
- Coding requests
- Web searches
- Explanations
