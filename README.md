# Strategic Workforce Planning — Application

Multi-page Streamlit app. Each configuration screen and each function is its
own file under `pages/`.

## ⚠️ Important: this update changes the database schema

`WfpRow` now has a `plan_id` column, and `PlanConfig` is no longer a
singleton (it's a real table of plans). **If you have an existing
`swp_app.db` from a previous version, delete it before running this
version** — the old schema is incompatible and the app will error out
otherwise. A fresh `swp_app.db` is created automatically on first run.

## Structure

```
swp_app/
├── Home.py
├── common.py
├── database.py
├── master_data.py
├── master_data.xlsx
├── requirements.txt
└── pages/
    ├── 1_System_Configuration.py
    ├── 2_Application_Configuration.py
    ├── 3_Function_1_Demand_Entry.py
    ├── 4_Function_2_Supply_Plan.py
    ├── 5_Function_3_Rampdown_Attrition.py
    └── 6_Function_4_Generate_WFP.py
```

## Run it

```bash
pip install -r requirements.txt
streamlit run Home.py
```

## Latest fixes

- **No way to switch back to an existing plan** — previously, once a plan
  locked, "Create new plan" was the only path forward; there was no way to
  select an already-existing plan (locked or not) as active again.
  **Application Configuration now has a "Select active plan" dropdown**
  (shown whenever more than one plan exists) listing every plan with its
  status, letting the user switch the active plan at any time — including
  back to a locked one — without creating anything new or touching any
  plan's data.
- **Plan name wasn't visible on the working screens** — only shown in the
  sidebar caption. **Functions 1–4 now display a banner right under the page
  header** stating the active Workforce Plan's name and whether it's
  🟢 Active/unlocked or 🔒 Locked, so it's always unambiguous which plan
  you're working in.

## What changed this round

### 1. Multiple Workforce Plans are now supported

Previously there was only ever one plan; once it locked (on first Generate
WFP), the application had no way to start a new one. Now:

- **Plans are a real list**, not a singleton. Exactly one plan is "active"
  at a time — that's the plan Functions 1, 2, 3, and 4 all operate against.
- **Application Configuration** now has three states:
  - No plan exists yet → create the first one.
  - Active plan exists and is unlocked → edit its name/window freely.
  - Active plan is locked → its details show read-only, **and a "Create new
    plan" form appears** — creating one deactivates the old plan (which
    keeps its own data and locked status untouched) and makes the new plan
    active for all further work.
  - A **Plan history** list at the bottom shows every plan ever created,
    with its window and Active/Inactive + Locked/Unlocked status.
- **Data is fully isolated per plan.** Every `WfpRow` now belongs to a
  specific plan (`plan_id`). This was tested directly: entering demand
  against the *same* Customer/Location/Date/Technology/Grade/Service Line
  key under two different plans creates two independent rows — no
  collision, and Plan B's queries never see Plan A's data. Functions 1–4 all
  operate only against the currently active plan.

### 2. System Configuration is hidden once locked

- The **System Configuration** page no longer lists the toggle states or
  column names once locked — it just shows a short "locked, not displayed"
  message.
- **Home** now explicitly states: "System Configuration parameters are
  locked and are not displayed."
- The sidebar caption was simplified the same way — it says "System
  configuration locked" without listing which optional columns are active.

Note: this only hides the *display* of the settings from the user —
Functions 1–3 still use the actual toggle values internally to decide which
fields to show/require; that functional behavior is unchanged.

## First steps

1. **System Configuration** — one-time toggles + column renaming, locked
   together on save.
2. **Application Configuration** — create your first Workforce Plan (name +
   Start/End Date window).
3. **Function 1 / 2 / 3** — enter demand, supply, and rampdown/attrition for
   the active plan.
4. **Function 4** — Generate WFP. This locks the active plan's details. Go
   back to **Application Configuration** afterward to create a new plan and
   continue working.

## Master data

`master_data.xlsx` — unchanged from the previous version. See its own
section in the Functional Requirements document for sheet/column details.

## Known simplifications / things to validate against your real requirements

- Function 2's screen shows all demand rows for a Customer/Location/Start
  Date within the active plan, even across multiple Primary Technology/Grade
  combinations under that date — intentional, confirmed earlier.
- Data is stored in local SQLite (`swp_app.db`).
- Analytics is not implemented yet.
