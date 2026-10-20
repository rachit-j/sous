# Cooking tools

See the [V2 contracts](v2-api.md), [recipe library](recipes.md) and [account boundaries](accounts.md).

## Preparation and timers

Starting a cook saves a recipe snapshot. Its ingredient and equipment checklist belongs to that session; editing the library later cannot change it. Checking items is optional and does not advance steps.

Create named timers alongside the existing quick timer. Names are limited to80 characters, durations to1–86400 whole seconds, with8 live timers and64 total per session. Timers use persisted absolute deadlines and continue through step changes and paused camera guidance. Dismiss an elapsed timer or cancel a running timer independently. Finishing or ending the cook cancels its remaining timers.

Keep Sous open and visible to receive timer notices, including while viewing Recipes or Settings. A hidden tab or closed app cannot provide timely notifications; returning detects overdue timers. Active voice can announce expiry, but does not change timer state.

## History and notes

History lists your completed and ended cooks, newest first, with their saved recipe snapshots and recent events. Add a private note of up to4000 characters. Notes belong to the cook rather than the shared recipe. Other accounts, including administrators, cannot read your private history through these APIs.

## Advisory camera alerts

Camera observations may include a hazard or possible-emergency concern, visible evidence and an obstructed-view flag. These are uncertain model judgments from intermittent still images. They cannot establish food safety, continuous supervision or absence of danger. Sous never contacts emergency services or controls an appliance.

An obstructed view or safety concern cannot count toward automatic step advancement. Manual controls remain available. Repeated identical concerns within60 seconds are deduplicated; acknowledging an alert records your response, not a model claim that the concern has disappeared. Only a fresh result authorized against the current account, cooking revision and credential lease can create an alert.

The monitoring display distinguishes paused, camera off, pending, recent, stale, error and obscured states. Server freshness uses successful observations, not button activity; evidence becomes stale after the greater of30 seconds or three configured snapshot intervals. “Recent” describes a check's timing and never means the kitchen is safe.
