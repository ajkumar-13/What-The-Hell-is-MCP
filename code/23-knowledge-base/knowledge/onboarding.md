# Onboarding

How a new engineer on the Orchard platform team gets from a fresh laptop to a
merged pull request. Budget three days, and pair with your buddy for all of it.

## Day one: accounts and access

Your manager files the access request the Friday before you start. By the time
you sit down you should have four things. If any of them is missing, say so in
the team channel rather than waiting.

1. A single sign-on account, which is what every other system checks.
2. Membership of the `orchard-engineers` group, which grants repository write.
3. A seat in the credentials vault, where every shared secret lives.
4. A pager schedule entry, initially marked shadow so you are never paged alone.

Nobody on this team emails a secret, pastes one into a chat, or commits one to a
repository. Everything comes out of the vault, and the vault is the only place
it is allowed to exist. If you find a secret checked into source control, treat
it as an incident and rotate it. Rotation steps live in the runbooks document.

## Day one: local setup

The platform is four services and a Postgres database. You can run all of it on
a laptop, and you should, because the staging environment is shared and slow to
iterate against.

Install the toolchain first:

- Python 3.11 or newer.
- `uv`, which manages both the interpreter and the dependencies.
- Docker, for Postgres and the message broker.
- The `orchard` command line tool, installed with `uv tool install orchard-cli`.

Then clone and build:

    git clone git@github.com:orchard/platform.git
    cd platform
    uv sync --all-extras
    orchard dev up

`orchard dev up` starts Postgres, the broker, and all four services with hot
reload. It takes about ninety seconds the first time because it has to pull
images. When it is ready it prints the local gateway address.

## Day two: run the stack

Verify the stack before you change anything. A broken checkout looks exactly
like a broken change, and telling the two apart later costs an afternoon.

    orchard dev check

That command runs the migration check, hits the health endpoint of every
service, and places one test payment through the whole path. All four services
should report `ok`. If the ledger reports `degraded`, your Postgres container is
probably still applying migrations; wait and run it again.

The test suite is next:

    uv run pytest

The suite is about nine hundred tests and finishes in roughly two minutes on a
laptop. Tests that need the database are marked `integration` and are skipped
automatically when `orchard dev up` is not running.

## Day two: your first change

Pick something from the `good first issue` label. The point of the first change
is to exercise the whole path, not to be useful, so a documentation fix or a
better error message is a perfectly good choice.

The workflow is:

1. Branch from `main`. Branch names are `yourname/short-description`.
2. Make the change, and add or update a test that would fail without it.
3. Run `uv run pytest` and `uv run ruff check .` locally.
4. Open a pull request. The template asks what you changed and how you tested.
5. Get one approving review. Any engineer on the team can approve.
6. Merge. Continuous integration deploys to staging on merge.

Production is a separate, manual promotion. Nothing you merge reaches customers
without somebody clicking the promote button in the release workflow.

## Day three: on-call shadowing

You join the pager rotation as a shadow in your second week and as a primary no
earlier than your second month. Shadowing means you get every page the primary
gets and you write the timeline, while the primary drives.

Read the runbooks document before your first shadow shift. You are not expected
to have memorized it. You are expected to know that it exists and to open it
during the incident rather than improvising.

## Who to ask

- Anything about access, the vault, or the pager: your manager.
- Anything about the ledger or the reconciliation job: the payments group.
- Anything about the gateway, deployment, or the build: the platform group.
- Anything that is on fire right now: the person currently on call, by pager.

Asking in the team channel is always acceptable. There is no question that
costs the team more than an engineer who is quietly stuck for two days.
