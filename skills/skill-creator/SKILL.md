# Skill Creator

Guide for creating new skills for the Tsunami agent.

## Creating a Skill

1. Create a directory under `skills/` with a descriptive name
2. Add a `SKILL.md` file with:
   - Title (first line, as # heading)
   - One-line description (second non-empty line)
   - Detailed instructions
   - Quality standards
   - Output format expectations
3. Optionally add scripts, templates, or reference documents

## Structure

```
skills/
  my-skill/
    SKILL.md          # Required — instructions
    templates/        # Optional — output templates
    scripts/          # Optional — helper scripts
    references/       # Optional — reference docs
```

## Best Practices

- Keep instructions actionable and specific
- Define clear quality criteria
- Include example outputs when possible
- Skills should be self-contained — don't depend on external state
