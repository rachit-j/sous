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

Restart local Flask after editing recipes. For Docker, rebuild with `docker compose up --build` so the JSON files are included in the image. Bundled files are read-only for every account, including administrators; copy one into the library to change it.

## Schema

The same validation applies to bundled files, library saves and edits, and AI-generated drafts.

| Field | Rule |
| --- | --- |
| `id` | Bundled files only. Library and generated recipes receive an opaque server ID; any supplied ID is ignored. |
| `name`, `description` | Nonempty plain text, at most 2000 characters. |
| `ingredients` | Up to 64 display strings such as `"2 tbsp butter"` (V1 compatible). |
| `steps` | 1–64 objects with nonempty `title`, `instruction` and `visual_cue`. |
| `servings` | Optional integer 1–24; library recipes default to 1. |
| `equipment` | Optional list of up to 32 strings; library recipes default to `[]`. |
| `ingredient_details` | Optional list aligned one-to-one with `ingredients`: `{name, quantity, unit}` where `quantity` is a finite number above 0 and at most 100000, or `null` when unknown, and `unit` is text of at most 40 characters (may be empty). |

Text containing markup such as `<script>` or `javascript:` is rejected. This is defense in depth; the web client still renders recipe text as text. Unknown fields are dropped.

## Library

Accounts see bundled recipes, global recipes added by administrators, and their own personal recipes. Administrators cannot read other accounts' personal recipes. Each account may keep 200 personal recipes and the instance 200 global database recipes. Deleting a library recipe archives it: it leaves the library and can no longer start a cook, but sessions keep their own snapshot. API envelopes are in [V2 API contracts](v2-api.md).

## Serving drafts

`POST /api/recipes/<id>/scale` returns an unsaved `{recipe, warnings}` draft. Only `ingredient_details` entries with a numeric quantity are multiplied (rounded to three decimals) and their display strings rebuilt as `quantity unit name`. Ingredients without a known quantity stay unchanged with a warning. Steps, times and temperatures are never changed, and results above 100000 are rejected.

## AI drafts

`POST /api/recipes/generate` uses the caller's own unlocked AWS connection with a text-only Bedrock Converse call: the saved default model when the default provider is Bedrock, otherwise the connection's model. There is no environment or local-model fallback and no automatic retry. The reply must be exactly one JSON recipe (plain or in one code fence) of at most 32 KiB that passes the schema above; otherwise the request fails with 502 and nothing is stored. Drafts are never saved automatically and always carry a review warning. Each account may run one generation at a time and six per minute; the instance runs at most two at once. Logout, password changes or vault changes during the call discard the draft.

The bundled **Desk demo — hand gestures** recipe uses four static hand poses and no food or materials. It exercises the real camera and configured model pipeline; it does not use canned responses, and gesture recognition is not guaranteed. Keep each pose steady and facing the camera. At the default10-second interval, automatic advancement may take20–30 seconds or longer for slow models; Settings can adjust the interval from1–15 seconds. You can select **Analyze now** twice or use **Next** manually. Finish the final step manually.

## Stable sessions

A session retains the recipe definition it started with. Editing or removing a recipe file does not rewrite an active or historical session's step instructions. Reloading still pauses camera monitoring until the cook resumes.

See [API contract](api.md), [architecture](architecture.md), and [plan](../PLAN.md).
