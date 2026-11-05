# Back Arrow & Forward Arrow — How They Work

## Overview

The back (←) and forward (→) buttons are **not** a general browser-style history.  
They are a **one-step undo/redo specifically for the "Clear All" action**.

They are disabled by default and only become active after the user clicks **Clear All**.

---

## The Two State Variables

```js
let preClearSnapshot = null;  // the filter/slicer state saved just before Clear All
let inClearedState = false;   // true = we are currently in the blank/cleared state
```

These two variables are the entire "memory" of the feature.

---

## Step-by-Step Flow

### 1. User clicks "Clear All"

```js
// Confirmation overlay → user clicks "Clear All"
preClearSnapshot = await snapshotCurrentState();  // snapshot saved here
inClearedState = true;
updateNavButtons();  // ← back button becomes enabled
```

`snapshotCurrentState()` captures:
- All report-level filters
- All slicer selections across every visited page

---

### 2. User clicks ← (Back)

```js
if (!preClearSnapshot || !inClearedState) { return; }  // guard

inClearedState = false;
updateNavButtons();                     // disable back, enable forward
applyFilters(preClearSnapshot);         // restores everything to pre-clear state
```

---

### 3. User clicks → (Forward)

```js
if (!preClearSnapshot || inClearedState) { return; }  // guard

inClearedState = true;
updateNavButtons();                                    // enable back, disable forward
applyFilters({ filters: [], slicers: [] });            // re-applies the blank/cleared state
```

---

## Button Enable/Disable Logic

```js
function updateNavButtons() {
    back.disabled = !preClearSnapshot || !inClearedState;
    fwd.disabled  = !preClearSnapshot ||  inClearedState;
}
```

| Situation                    | Back  | Forward |
|------------------------------|-------|---------|
| Page just loaded             | Off   | Off     |
| After Clear All              | **On**| Off     |
| After clicking ← Back        | Off   | **On**  |
| After clicking → Forward     | **On**| Off     |
| After applying a Saved View  | Off   | Off     |

> Applying a saved view resets `preClearSnapshot = null` and `inClearedState = false`,
> which disables both buttons.

---

## What `applyFilters()` Does

When called with the snapshot, it:
1. Sets report-level filters via the Power BI JS SDK
2. Arms the slicer states so they are applied page-by-page as the user navigates
3. Immediately applies slicers to the currently active page

---

## Key Files

| File | Relevant Lines |
|------|---------------|
| `templates/index.html` | Lines 388–410 — state variables + back/forward handlers |
| `templates/index.html` | Lines 391–396 — `updateNavButtons()` |
| `templates/index.html` | Lines 1063–1117 — Clear All confirmation handler (where snapshot is taken) |
| `templates/index.html` | Lines 521–566 — `applyFilters()` |
| `templates/index.html` | Lines 626–632 — `snapshotCurrentState()` |

---

## Summary (One Line)

> Back/Forward toggle between **your last filter state** and **a blank slate** — they exist purely to undo/redo a "Clear All".
