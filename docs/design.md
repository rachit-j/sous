# Sous interface design

The visual system for the Sous cooking workspace and V2 account surfaces. Implementation lives in [static/style.css](../static/style.css), markup in [templates/index.html](../templates/index.html), and dynamic markup in [static/app.js](../static/app.js) and [static/v2.js](../static/v2.js). Behavior and state rules are in [Architecture and cooking state](architecture.md), the [API contract](api.md), and the [V2 API contract](v2-api.md).

## Design intent

Sous is an operating tool used at a stove: glanced at from a distance, touched with busy hands, and read in short bursts. The interface is quiet and familiar so the current step can carry the screen.

- Near-white and cool-grey surfaces, charcoal type and primary actions.
- Color appears only to communicate state: green for selection, active guidance and completion; blue for focus, a running timer and model confidence; amber for attention (restored/paused session, finished timer, unavailable recipe); red for errors.
- No gradients, decorative illustrations, emoji icons, hero sections, eyebrow labels or metric tiles.
- Recipe content is always rendered from the selected recipe data or the session's recipe snapshot. No copy, layout or styling depends on a particular recipe, its name or its step count.

## Tokens

Defined as custom properties on `:root`.

| Role | Token | Value | Use |
|---|---|---|---|
| Page | `--bg` | `#f4f5f7` | Page background, sunken insets |
| Surface | `--surface` | `#ffffff` | Panels, inputs |
| Hover surface | `--surface-hover` | `#eef0f3` | Hover fills, step number tile, video placeholder |
| Divider | `--line` | `#e1e4e9` | Panel borders, separators |
| Strong line | `--line-strong` | `#c9ced6` | Secondary button and choice borders |
| Control border | `--control-border` | `#868d99` | Text inputs and selects (≥3:1 on white) |
| Ink | `--ink` / `--ink-2` / `--ink-3` / `--ink-4` | `#1b1f24` / `#353b44` / `#555c67` / `#646b77` | Headings / instruction body / secondary / captions (all ≥4.5:1 on page and surface) |
| Primary | `--primary` / `--primary-hover` | `#1f2329` / `#0d0f12` | Primary buttons, progress fill, current recipe step |
| Green | `--green`, `--green-ink`, `--green-soft`, `--green-line` | | Selected mode, guidance on, camera on, completed steps, completion |
| Blue | `--blue`, `--blue-ink`, `--blue-soft` | | Focus ring, running timer, confidence pill |
| Amber | `--amber`, `--amber-ink`, `--amber-soft`, `--amber-line` | | Notices, finished timer |
| Red | `--red`, `--red-soft`, `--red-line` | | Error messages, error toasts, invalid inputs, connection problem |

Radii: panels `12px`, controls `8px`, badges and pills fully rounded. Elevation is declared once: panels use a 1px border; only floating elements (correct-step popover, toast) use `--shadow-float`.

## Typography

One system sans stack (`ui-sans-serif, system-ui, …`) with a fixed rem scale; no web fonts or external assets.

| Element | Size | Weight |
|---|---|---|
| Current step title (`#step-title`) | 2rem desktop, 1.5rem phone | 650 |
| Step instruction | 1.125rem / 1.6 line height, max 62ch | 400 |
| Page titles (setup, completion) | 1.75rem | 650 |
| Recipe name (session bar, setup preview) | 1.375rem | 650 |
| Panel headings | 0.9375rem | 650 |
| Body, controls | 0.875–0.9375rem | 400–600 |
| Captions and help | 0.8125rem; footnotes 0.75rem | 400 |

Numbers that change (timer, step counts, elapsed time, confidence, event times) use tabular figures.

## Layout

### Navigation and settings

The top bar has internal **Cooking**, **Recipes**, **History**, and **Settings** navigation, plus the signed-in user's name and **Log out**. Switching views does not reload the document. Leaving an active cooking session pauses guidance, stops voice, and cancels any pending observation before another view appears; returning to Cooking never resumes guidance automatically. A locked AWS vault shows an unlock notice and keeps manual step controls and timers available. The app asks for authentication before requesting account settings or cooking data. First use shows either a first-administrator setup form or a sign-in form.

Settings uses the same grey surfaces, charcoal type and compact panel treatment as the cooking workspace. The vault panel shows create, unlock or lock state. Bedrock fields include region, write-only access-key credentials, optional token removal, explicit model discovery and a manual model/profile ID. Saved secrets never populate form fields. Provider defaults apply to new sessions; existing sessions keep their provider/model. The snapshot interval is a separate preference, available while the vault is locked, with an integer range of 1–15 seconds and default of 10.

The cooking setup keeps recipe selection and preview as the main pane. Guidance shows a compact provider/model summary; per-session provider and model overrides sit in a disclosure. A saved but unavailable Bedrock default remains selected and disables **Start cooking** until the user unlocks or chooses an available override; it does not silently switch providers. Recipe selection includes the signed-in user's personal recipes and recipes shared globally.

### Recipes and accounts

Recipes is a searchable library with explicit shared/personal labels. Global recipes offer **Copy to my recipes**; users can edit and delete only their own recipes. Administrators can create additional global recipes. Recipe editing uses named fields and repeatable ingredient/step rows, not raw JSON. V1 free-text ingredients remain editable; optional amount and unit fields support scaling where quantities are known. AI generation uses explicit dish, ingredient, pantry, equipment, servings, and time inputs, then opens an editable draft with warnings. A draft is saved only after the user submits **Save recipe**.

History lists only the signed-in user's sessions. Selecting a session opens its saved recipe snapshot, events, and private note field. Settings contains that user's password controls, profile defaults, AWS vault, and—only for administrators—account creation and password reset.

### Cooking tools

The existing quick timer remains available. A separate named timer form creates independent timers, each with its own countdown, acknowledge action, and cancel action. The prep checklist is session-specific and checkable. Monitoring health names paused, camera-off, pending, recent, stale, failed, and obscured views; timestamps identify the last successful look. Advisory hazard and possible-emergency panels show model evidence and an explicit acknowledgment. Copy states that snapshots are intermittent and cannot confirm safety or contact emergency services.

### Setup

A single framed form split into two panes.

- **Recipe pane:** the recipe select and a live preview generated from the recipe data: name, step and ingredient counts, description, ingredient list and numbered step titles. The ingredient and step lists sit side by side when the pane is at least 520px wide (container query) and stack otherwise.
- **Guidance pane:** compact default provider/model summary, optional per-session provider/model disclosure, mode choice (radio cards, green when selected), the camera privacy note and a full-width **Start cooking** button.
- Below 960px the panes stack.

### Active cooking

- **Session bar:** the recipe name as the visible `h1` (the current step title is included as visually hidden text for assistive technology), plus mode and guidance badges.
- **Notices:** amber restore and unavailable-recipe notices sit above the workspace.
- **Step column (left, primary):** progress line, the current step (number tile, title, instruction, "What to look for" cue), then step actions: **Back** on the left, **Correct step** and the primary **Next step** / **Finish cooking** on the right. Guidance pause/resume and **End session** sit below a divider. On wide, tall screens (≥960×760) this column is sticky so the step stays in view while the tool column scrolls.
- **Tool column (right, 360px):** Timer, Kitchen view (camera), What Sous sees (observation with confidence caveat), and the collapsible Recipe outline. Completed steps show a green check, the current step is charcoal, upcoming steps are outlined.
- **Lower row:** voice and text commands beside session notes.
- 640–959px: one main column, tools in a two-up grid. ≤640px: everything stacks in task order (step, timer, camera, observation, recipe, commands, notes); **Next step** becomes a full-width first action, with Back and Correct step sharing the row beneath.

The correct-step form is a popover anchored to the step-action row. It is right-aligned at 340px on desktop and spans the full action row on phones, so it never extends past the viewport or gets clipped by a parent.

### Completion

A centered panel with a status mark, the `h1` (`Cooking complete.` or `Session ended for now.`) and **Start another session**. `#completion-panel[data-status]` switches the mark between a green check (completed) and a neutral saved mark (ended).

## Components and states

- **Buttons:** `button-primary` (charcoal), `button-secondary` (white with grey border), `button-ghost` (text only); sizes `button-small` (36px), default (44px), `button-large` (48px). Every variant defines hover, active, disabled and the shared focus ring. Icons are inline SVG with a consistent 1.75 stroke, always `aria-hidden`.
- **Inputs and selects:** 44px default height (36px in compact tool forms), charcoal-grey border, blue focus ring, muted disabled fill, red border and ring for `:user-invalid` or `aria-invalid="true"`.
- **Focus:** `2px` blue outline with `2px` offset on every focusable element. Radio cards show the ring on the card.
- **Messages:** camera and voice status lines are grey, or red with `.error`. Toasts are charcoal, or red-tinted with `.error`.
- **Timer readout:** `#timer-readout[data-state]` is `idle` (grey), `running` (blue, with blue panel border) or `done` (amber panel). app.js sets the attribute in `updateTimerDisplay`.
- **Camera indicator:** an outlined dot that fills green when the camera is on. The caption text always states the camera state, so color is never the only signal.

## Motion

- Step changes: `#current-step` receives `.step-changed`, a 260ms fade/rise from an already visible state. app.js adds the class only when the session or step index changes.
- Analysis: three grey dots beside the Kitchen view's "Analyzing" caption pulse and rise slightly while an observation request is pending. They are decorative (`aria-hidden`); the existing camera status announces analysis once per request. Reduced motion shows static dots. Completion, errors and cancellation clear the indicator; the previous observation stays visible, with its panel marked busy while analysis runs.
- Panels arrive with a 180–240ms fade/rise. Progress width, popover chevrons and hover colors transition in 150–300ms.
- `prefers-reduced-motion: reduce` effectively disables animations, transitions and smooth scrolling.

## Dynamic markup contract

Classes and attributes created by app.js that the stylesheet relies on:

| Source | Markup |
|---|---|
| `updateRecipePeek` | `#recipe-peek` › `.peek-head` (`h2.peek-title`, `p.peek-meta`), `p.peek-description`, `.peek-columns` › `.peek-group` (`h3.list-label`, `ul.peek-ingredients` / `ol.peek-steps`) |
| `renderRecipeLists` | `#ingredient-list li`, `#recipe-steps li` with `.current` and `aria-current="step"` |
| `renderObservation` | `p.observation-text`, `p.evidence-text`, `#confidence-pill` |
| `renderEvents` | `#event-list li` (`time`, `p`) or `li.empty-events` |
| `setConnection` | `.connection-state.ready` / `.problem` |
| Monitoring and camera | `.monitoring-badge.active`, `#camera-indicator.on` |
| Messages | `.camera-message.error`, `.voice-message.error`, `.toast.error` |
| Analysis pending | `#analysis-dots[hidden]` toggles decorative dots; `#observation-panel[aria-busy=true]` marks pending observation updates |
| Timer | `#timer-readout[data-state=idle|running|done]` |
| Step change | `#current-step.step-changed` |
| Completion | `#completion-panel[data-status=completed|ended]` |
| Navigation/settings | `#app-nav` selects `#setup-panel`, `#session-panel`, `#completion-panel` or `#settings-panel`; settings messages use text-only status regions and safe server-provided metadata |

## Verification

The voice panel has explicit Start/Stop, a status region and bounded temporary conversation text. It explains AWS audio sharing and keeps text/manual controls available. Settings selects the speaking voice independently of the camera model. No microphone starts automatically; Settings, hidden tabs, session end and lock close it. Listening/speaking states use the existing green/blue accents without required motion. See [voice lifecycle](voice.md).

The WebKit suite ([tests/test_browser.py](../tests/test_browser.py)) captures setup, cooking and completion at desktop/mobile sizes under `/private/tmp/sous-webkit-artifacts`, plus `cooking-desktop-viewport.png`. It resets scroll instantly before captures and checks step-column alignment at page top. It asserts there is no horizontal overflow at 1280px and 390px, and that the correct-step form is fully visible and unclipped at both widths. Results are recorded in [Validation evidence](validation.md).
