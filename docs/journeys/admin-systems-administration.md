# Systems administration

*Admin-only guide. Unlike the numbered journeys, this one is not listed in the in-app Help; it lives
in the repo for administrators. It covers the **Settings** page and its tabs: models and pricing,
execution guardrails, the AI prompts, and user management.*

**Who / when:** administrators setting the tool up or keeping it running.
**Pages used:** **Settings** (`/settings`, admins only), the **User administration** page
(`/admin/users`), and the per-provider peak-hours editor.

# Journey Overview

Almost everything a normal user does needs no admin. Administration is the small set of behind-the
-scenes controls: which AI models are available and what they cost, how much the tool is allowed to
spend and how much it does per run, a read-only view of the prompts, and who has an account and what
role they hold. All of it lives on the **Settings** page, which only administrators can see, plus the
**User administration** page it links to.

The **Settings** link appears in the top navigation only for admins, and the page is checked again on
the server, so a non-admin can neither see nor reach it. The page has four tabs: **Available Models**,
**Execution Guardrails**, **AI Prompts**, and **User Management**.

---

## Available Models

**What you do:** on the **Available Models** tab, manage the AI models the tool can use. The table
lists each model by supplier and model ID, with its full price grid: input tokens (cache hit and
cache miss, each at off-peak and peak rates) and output tokens (off-peak and peak). You can mark one
model as the **inclusion** model and one as the **exclusion** model, so each phase runs on the model
you choose. A model whose provider has no key configured is flagged, so you can see at a glance which
models are actually usable. **Add a model** with the form below the table (supplier, model ID, and
its rates), and open **Peak / off-peak hours** to set each supplier's weekly peak schedule.

**What you see:** the models available to a run, the rate the cost engine will charge, and which
model each phase currently uses.

> Say: "This is the catalogue of models and their prices. Pick which model runs the inclusion pass
> and which runs the exclusion pass, add a new model with its rates, and set each supplier's peak
> hours so the cost is worked out correctly."

![Screenshot: the Available Models tab, the models table with the price grid, the "Add a model" form, and the Peak / off-peak hours link](screenshots/admin-1-models.png)

**How this helps you:** models and prices change often. Keeping them here, rather than in code, means
you can switch models, add a new one, or correct a rate without a deploy, and every run is costed
against the current numbers.

## Execution Guardrails

**What you do:** on the **Execution Guardrails** tab, set two limits. The **Daily AI budget (USD)**
caps total spend across all AI calls in a day; the tool warns within 10% and stops when the day's
spend reaches it, and 0 means no limit. **Maximum documents per run** caps how many pages a single
run will process. The tab also shows how much has been spent today against the budget. Press **Save
guardrails**.

**What you see:** the day's spend so far, and the two limits that protect it.

> Say: "Two safety limits. A daily budget, so a runaway job can't spend the month in an afternoon,
> and a per-run page cap, so a run does a sensible batch and then stops for you to continue."

![Screenshot: the Execution Guardrails tab, showing spent-today against budget, the Daily AI budget field, and the Maximum documents per run field](screenshots/admin-2-guardrails.png)

**How this helps you:** the budget is the main defence against surprise cost, and the per-run cap
keeps a large shortlist moving in steady batches rather than one unbounded run. When a run stops for
either reason, its Run outcome says so, and the user carries on with Complete run.

## AI Prompts

**What you do:** on the **AI Prompts** tab, read the prompts behind each AI feature. They are shown
read-only, because the prompts live in **git** (`govuk_corpus/evaluate.py`, `prompts.py`,
`category_interview.py`), which is the single source of truth. Each prompt shows its version (a git
short SHA), the placeholders it fills in at run time, and its full text.

**What you see:** exactly what the AI is asked, and which version is current.

> Say: "The prompts are here to read, not to edit. They live in git, so changing one is a code
> commit rather than an in-app change, and every run records the exact prompt it used, so old runs
> stay reproducible whatever changes later."

![Screenshot: the AI Prompts tab, a prompt expanded read-only, with its git version and placeholders](screenshots/admin-3-prompts.png)

**How this helps you:** you can always check what the AI is actually being asked, and satisfy anyone
who wants to see it, without risk of an accidental edit changing behaviour mid-flight.

## User Management

**What you do:** on the **User Management** tab, go to the **User administration** page. There you
view and edit every account, add new ones, and set each person's role. This tab and page apply when
the app runs in accounts mode (`AUTH_MODE=accounts`); in shared-password mode there are no individual
users to manage.

**What you see:** the list of accounts with their roles and status, and the controls to add a user,
change a role, or send a password reset.

> Say: "User management is a link through to the User administration page, where you add people,
> change roles, and reset passwords. The two roles that matter day to day are User and Admin."

![Screenshot: the User administration page, the list of accounts with role and status, and the add-user and edit controls](screenshots/admin-4-users.png)

**How this helps you:** roles decide who can reach this admin area at all. Grant Admin only to the
people who should manage the tool; everyone else is a User.

### The break-glass admin

There is one permanent emergency admin, **breakglass_admin@defra.gov.uk**. It is always allowed in
with the password held in the server environment (`BREAKGLASS_ADMIN_PASSWORD`), even if the database
is in a bad state, and it cannot be demoted, disabled or locked out. Use it to get in and grant a
normal Admin account (or to recover if admin access is ever lost), rather than as a day-to-day login.
Other admins are created the ordinary way, on the User administration page. Rotate the break-glass
password by changing that environment value and restarting.

## A note on the other admin-only areas

The top navigation shows a few more admin-only links that these journeys don't cover: **GOV.UK
search** (a direct search tool) and **AI Assistant** (a scratch prototype for trying prompts against
the model). They are not part of the core build-a-shortlist workflow, and the AI Assistant in
particular is experimental.

## Links

- The everyday workflow these settings support starts at [Journey 2: What is a shortlist?](2-what-is-a-shortlist.md).
- Budgets and per-run limits show up to users in [Journey 4: Run the AI](4-run-the-ai.md).
- Models and prices are the same catalogue the cost figures in [Journey 6: How was this built?](6-how-was-this-built.md) draw on.
