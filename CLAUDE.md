# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Activate the virtualenv first: `source venv/bin/activate`.

- Install deps: `pip install -r requeriments.txt` (note the spelling)
- Run dev server: `python manage.py runserver`
- Migrations: `python manage.py makemigrations` / `python manage.py migrate`
- Create superuser: `python manage.py createsuperuser`
- Run tests: `python manage.py test` — single app: `python manage.py test aplications.users` — single test: `python manage.py test aplications.users.tests.TestClass.test_method`
- Production settings module: `admin.production` (set `DJANGO_SETTINGS_MODULE=admin.production`)

Environment variables (loaded via `python-dotenv` in `admin/databases.py`): `DEBUG_DJ`, `IP_SERVER`, `DOMAIN`, and DB vars (`DATABASE_DB`, `USER_DB`, `PASSWORD_DB`, `HOST_DB`, `PORT_DB`) when switching off SQLite.

## Architecture

Django 4.2 project. The project package is named **`admin`** (not the usual `config`/project name) — `admin/settings.py`, `admin/urls.py`, `admin/wsgi.py`. Do not confuse with `django.contrib.admin`, which is also mounted at `/admin/` in `admin/urls.py`.

Apps live under **`aplications/`** (sic — directory is misspelled and referenced that way in `INSTALLED_APPS` and imports; keep the spelling).

- `aplications/users` — auth, signup, profile, user-management panel. Owns the root URL include (`''` → `users` namespace).
- `aplications/vacations` — vacation-request workflow, mounted at `/vacaciones/` (namespace `vacations`).

### Database switching

`admin/databases.py` exports `SQLITE`, `POSTGRESQL`, `MYSQL` dicts. `admin/settings.py` selects one via `DATABASES = SQLITE`. Switch backends by changing that line (and installing the driver).

### Auth / authorization model

There is no custom user model. Authorization lives on `Profile` (`aplications/users/models.py`), 1:1 with `auth.User`. Everything is driven by a single `level: SmallIntegerField`:

| level | role |
|-------|----------------|
| 0 | Bloqueado |
| 1 | Trabajador (default) |
| 2 | Administrador |
| 3 | RH |
| 4 | Líder / Jefe / Gerente |
| 5 | Visualizador / Auditor |

Each level has a derived `@property` on `Profile` (`is_blocked`, `is_admin`, `is_hr`, `is_leader`, `is_auditor`) so templates and views can read `profile.is_leader` without hard-coding the integer. **Roles are exclusive** — a user has exactly one level. If you need someone to "be admin and leader," pick one; this is a deliberate simplification.

`_home_for` in `users/views.py` only branches on `level == 0` (→ `users:block`); everything else lands on `users:ping`. Fine-grained gating happens per view via `_is_admin` (users panel), `_is_hr`/`_is_leader` (vacations inboxes).

A `post_save` signal `ensure_profile` (`aplications/users/models.py`) creates a `Profile` for every new `User`, so `user.profile` is always safe to read. The reverse accessor uses Django's default name (`user.profile`) since the OneToOne has no explicit `related_name`. New profiles default to `level=0` (bloqueado) — an admin has to promote them.

`Profile.boss` is a single FK to another `User` (`limit_choices_to=` is not enforced at the field; the views filter to `profile__level=4` when offering options). Workers submit vacation requests to their `boss`; if `boss` is unset, the request still gets created but with no `requested_to_leader` set, and HR can still see/act on it.

### Vacations domain

Single model `VacationRequest` (`aplications/vacations/models.py`) drives a two-stage approval workflow:

1. Employee submits → `PENDING_LEADER`, with `requested_to_leader = profile.boss`. Leader approves → `PENDING_HR`. HR approves → `APPROVED`. Either stage can `REJECTED` it.
2. A leader can submit *on behalf of* a worker via the same `create_request` view with `as_leader=1`; that path skips the leader stage and lands in `PENDING_HR` with `origin=LEADER`.
3. Cancellation rules (`cancel_request`): the owner can cancel only while the date is still in the future; leaders and HR can cancel any non-terminal request at any time.

`Kind` is `FULL_DAY` or `PARTIAL`. Partials ("acumulables") require an `event_time` validated by `_validate_partial_time` against the hardcoded workday (08:00–18:00) — late arrivals 08:00–11:00, early leaves 14:00–18:00.

**No balance / saldo feature.** Vacation-day budgeting was removed — `services.py` is gone. If you need per-employee day limits, that's a new feature, not a regression.

The calendar UI (`templates/vacations/calendar.html`) hits `events_json` with `scope=mine|all`. `scope=all` is only honored for leaders/HR; everyone else is silently filtered to their own requests — preserve that guard when editing.

### Templates / static

Global `templates/` dir (configured in `TEMPLATES.DIRS`) plus per-app template dirs via `APP_DIRS=True`. Static collected to `staticfiles/`; media uploads go to `media/` (served in dev via `admin/urls.py`).

### Deploy

`deploy/gunicorn.sh` and `deploy/server.sh` exist but are empty placeholders.
