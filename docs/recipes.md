# Structured recipes

Sous tracks whichever structured recipe is selected. Scrambled eggs is the first bundled example; there are no eggs-specific prompts or fixed six-step assumptions in the cooking engine.

## Add a recipe

Add a JSON file under `recipes/`. Files load in filename order, so the bundled `00-scrambled-eggs.json` appears first. Each file contains one recipe object:

```json
{
  "id": "my-recipe",
  "name": "My recipe",
  "description": "A short description",
  "ingredients": ["Ingredient or item"],
  "steps": [
    {
      "title": "Prepare",
      "instruction": "What the cook should do",
      "visual_cue": "Visible evidence that this step is complete"
    },
    {
      "title": "Serve",
      "instruction": "Transfer to a serving dish",
      "visual_cue": "The finished food is on the serving dish"
    }
  ]
}
```

Use a unique nonempty ID, nonempty name/description, an ingredients list, and one or more steps with nonempty titles, instructions and visual cues. Invalid or duplicate definitions produce a clear startup error rather than being silently skipped.

Restart local Flask after editing recipes. For Docker, rebuild with `docker compose up --build` so the JSON files are included in the image. This MVP uses file-based definitions, with no recipe editor or AI recipe generation.

The bundled **Desk demo — hand gestures** recipe uses four static hand poses and no food or materials. It exercises the real camera and configured model pipeline; it does not use canned responses, and gesture recognition is not guaranteed. Keep each pose steady and facing the camera; because snapshots are taken every 10 seconds, automatic advancement may take 20–30 seconds. You can select **Analyze now** twice or use **Next** manually. Finish the final step manually.

## Stable sessions

A session retains the recipe definition it started with. Editing or removing a recipe file does not rewrite an active or historical session's step instructions. Reloading still pauses camera monitoring until the cook resumes.

See [API contract](api.md), [architecture](architecture.md), and [plan](../PLAN.md).
