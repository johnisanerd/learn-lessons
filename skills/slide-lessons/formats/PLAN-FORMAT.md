# PLAN.md Format

`PLAN.md` lives in each `decks/NNNN-<slug>/` folder. It is the lesson plan for that deck (or batch of decks). It is a living document: `next` ticks lessons off, inserts repair lessons, and records skips.

## Template

```md
# Plan: {Deck title, or batch name}

**Source**: {deck title} by {author or course}, {N} slides ({k} with speaker notes){, private}
**Mission link**: {one sentence: why this deck matters for MISSION.md}
**Coverage**: {one sentence: how much the deck explains on its own, e.g. "bullets only, no notes: most explanation comes from research"}

## Lessons

1. [x] **{Concept}**: {one-line win} ([lesson](lessons/01-{slug}.html)) · slides 2–5
2. [ ] **{Concept}**: {one-line win} · slides 6–9 🖼
3. [ ] **{Concept} (1/2)**: {one-line win} · slides 10–14
4. [ ] **{Concept} (2/2)**: {one-line win} · slides 15–17, 21
5. [ ] *(optional, off-mission)* **{Concept}**: {one-line win} · slides 18–20

## Skipped

- Slide 1 title, slide 2 agenda, slide 22 "Questions?", slide 23 thank-you
- Slides 12, 16 section dividers
- Slide 19 (hidden): backup chart, duplicate of slide 18
- {Concept} on slides 7–8: already known (see [LR-0003](../../learning-records/0003-{slug}.md))

## Changes

- {date}: inserted lesson 3a to repair {misconception} after recall check
```

## Rules

- **One concept per lesson, ~10 minutes to read and quiz.** Slide count doesn't set lesson size; concepts do. A dense diagram slide can be a whole lesson; ten bullet slides can be one.
- **Ranges can be non-contiguous**, and in a multi-deck batch they name the deck: `A: slides 3–6 + B: slides 2–4`.
- **🖼** marks a lesson whose meaning lives in a picture (diagram, chart, screenshot, photo). The lesson must explain it in words and embed the slide image.
- **Coverage** is honest about how much teaching the deck does by itself. Thin decks lean on research, and the lessons say which parts came from outside the deck.
- **Every slide is either in a lesson or on a Skipped line.** Nothing silently disappears. Hidden slides count too.
- **Don't rewrite history.** When the plan changes, edit the list and add a dated line under `## Changes`.
