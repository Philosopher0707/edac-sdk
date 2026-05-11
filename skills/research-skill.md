---
name: research-skill
description: Deep-dive research with structured output and source tracking.
applies_when: |
  Users need comprehensive research on a topic.
  Output must include sources, confidence scores, and key findings.
---

# Research Skill

## Principles
1. Always cite sources
2. Separate facts from speculation
3. Flag uncertain information with confidence scores (0-1)
4. Structure output as: Summary, Key Findings, Sources, Open Questions

## Research Workflow
1. Search and gather raw information
2. Fact-check against multiple sources
3. Synthesize into structured report
4. Highlight gaps that need human input

## Output Format
```markdown
## Summary
1-paragraph overview

## Key Findings
- Finding 1 (confidence: 0.95)
- Finding 2 (confidence: 0.82)

## Sources
- [Source 1]
- [Source 2]

## Open Questions
- What remains unknown?
```

## Failure Recovery
If information is contradictory:
1. Note the contradiction
2. State both viewpoints with confidence
3. Request human resolution if confidence < 0.7
