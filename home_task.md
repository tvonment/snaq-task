# Home Task: Nutrition Verification Agent


## Background

SNAQ is an app that helps people understand what they eat. A core challenge is reliability: food databases are incomplete, product data varies across sources, and LLM knowledge alone is not a trustworthy source for calorie counts and macros.

Your task is to build an agentic system that takes a set of food items as structured input and verifies their nutritional content.

---

## The Task

You are given a file called `food_items.json`. Each entry in the file represents a food item with an existing nutritional profile. Your agent should verify whether the provided nutrition data is correct, flag discrepancies, and where possible propose corrections.

**Input schema (one entry):**

```json
{
  "id": "chicken-breast-raw",
  "name": "Chicken Breast, Skinless, Raw",
  "brand": null,
  "category": "Meat & Poultry",
  "barcode": null,
  "default_portion": {
    "amount": 120,
    "unit": "g",
    "description": "1 medium breast"
  },
  "nutrition_per_100g": {
    "calories_kcal": 165,
    "protein_g": 31.0,
    "fat_g": 3.6,
    "saturated_fat_g": 1.0,
    "carbohydrates_g": 0.0,
    "sugar_g": 0.0,
    "fiber_g": 0.0,
    "sodium_mg": 74
  }
}
```

---

## Output

Structure the output however you think makes sense. There are no constraints on format. What matters is that it clearly communicates the verification result for each item.

---

## What we care about

**Agent design.** We want to see a deliberate system architecture, not just a single LLM call dressed up as an agent. How you structure the verification pipeline and why is a core part of what we're evaluating.

**Handling uncertainty.** Some food items are generic; some are branded. Some have barcodes; some don't. Some fields are inherently variable (e.g., farmed vs. wild fish, portion sizes). Show us how your system handles cases where sources disagree or data is incomplete.

**Tool use.** Your agent should interact with at least one external data source. The choice of tools and how you design their interfaces matters.

**Code quality.** Write code you'd be comfortable shipping. Clean abstractions, sensible error handling, no unnecessary complexity.

---

## Bonus: build a verification system for the agent

If you want to go further, add a layer that verifies the agent itself, not just the food data. This could take many forms. How you approach it is up to you.

This is optional and not a requirement for a strong submission. But it's the kind of thing we think about at SNAQ, so we're curious how you'd approach it.

---

## Constraints and freedom

- Use any LLM API and any agentic framework (or none at all).
- There is no time limit. Spend as much or as little as you want. We value focused, deliberate work over elaborate scope.
- Do not over-engineer. A simple, well-reasoned solution beats a complex one that you can't fully explain.

---

## What to deliver

A GitHub repo or a zip file containing:

1. **Working code** that we can run locally with minimal setup, taking `food_items.json` as input.
2. **A README** covering:
   - How to set up and run it
   - Your design decisions and why you made them
   - What you would do differently with more time
3. **The output** your agent produces on the provided `food_items.json`.
4. **Your AI conversation log.** We expect you to use an AI coding tool (Claude Code, Codex, Cursor, or similar). Please export and include the conversation or session transcript. This is not a gotcha: we actively want to see how you collaborate with AI tools, what you prompt for, and how you handle cases where the AI gets things wrong.

---

## How we evaluate

We read the README first. Then we run the code. Then we read the code.

We are not looking for the most sophisticated system. We are looking for evidence that you think clearly about agent design, make deliberate tradeoffs, and write code that reflects that thinking.