# Running MORPH on WSL2

Everything below runs inside the WSL2 Ubuntu shell. Ports bound to `127.0.0.1` in
WSL2 are reachable from Windows at `localhost`, so you browse from Windows and run
from Linux.

> **Keep the repository on the Linux filesystem** (`~/morph-hermes`), not under
> `/mnt/c/...`. Cross-filesystem I/O is an order of magnitude slower and the test
> suite and Playwright both feel it.

---

## 1. One-time setup

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip ffmpeg curl git
```

Node 20+ is needed for the browser suite. **Check what you have before installing
anything** — many WSL images already carry a recent Node:

```bash
node -v && npm -v
```

If that prints `v20` or newer, you are done; skip to the clone.

If Node is missing, install it from NodeSource, which ships `node` *and* `npm` in one
package:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource.sh
less /tmp/nodesource.sh
sudo -E bash /tmp/nodesource.sh
sudo apt install -y nodejs          # NOT "nodejs npm" — see below
```

> **Do not ask apt for `nodejs npm` together.** The NodeSource `nodejs` package
> bundles npm and declares `Conflicts: npm`, so requesting both is unsatisfiable and
> apt refuses with a long dependency wall. It changes nothing when it does — your
> system is fine, just drop `npm` from the command. If `npm` is somehow missing from
> an existing Node, `sudo corepack enable npm` restores it without apt.

```bash
git clone https://github.com/timioni001/morph-hermes ~/morph-hermes
cd ~/morph-hermes
make install
```

`make` is the paved path: every target runs through `.venv/bin/python`, so it works
the same whether or not you activated the virtual environment **in this particular
shell**. Activation does not survive a new terminal, an SSH reconnect or a WSL
restart, and on Ubuntu a bare `pip` outside a venv refuses with
`externally-managed-environment` (PEP 668) rather than saying what is actually wrong.

If you prefer to drive it by hand, always name the interpreter:

```bash
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/python -m pytest
```

Never a bare `pip` or `pytest`: with a venv active but empty, `pytest` falls through
`PATH` to a system install and reports errors from an interpreter you never chose.

```bash
# Chromium plus the Linux libraries it needs under WSL.
npm install --no-save playwright@1.49.1
npx playwright install --with-deps chromium
```

---

## 2. Prove it works, in four commands

```bash
make test
```

```bash
make e2e
```

```bash
make demo
```

```bash
make bundle
```

The first three take about a minute together. `make demo` is the one to watch: it
exits non-zero if any claim in it fails.

Each of these depends on the install, but the install is stamped — it re-runs only
when `pyproject.toml` changes, so the second and later commands start immediately
instead of reinstalling. `make reinstall` forces it if you ever need to.

---

## 3. See it

```bash
make serve
```

Then, from **Windows**, open:

| | |
|---|---|
| http://localhost:8820 | MORPH, through the Hermes dashboard plugin contract |
| http://localhost:8811 | the Storefront's own record page |
| http://localhost:8812 | the Helpdesk's |
| http://localhost:8813 | the Warehouse's |

Flag an order in MORPH, approve it, then reload the Storefront page. The status and
the version change there. That is the whole claim, and it is the shot the demo video
ends on.

---

## 4. Install it into a real Hermes

```bash
# Fetch the official installer, read it, then run it. Piping a network response
# straight into a shell is the one habit worth not teaching.
curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o /tmp/hermes-install.sh
less /tmp/hermes-install.sh
bash /tmp/hermes-install.sh
source ~/.bashrc
hermes setup --portal          # or: hermes model
```

```bash
ln -s ~/morph-hermes ~/.hermes/plugins/morph
hermes plugins doctor ~/.hermes/plugins/morph --ci
hermes plugins validate ~/.hermes/plugins/morph --install-deps
hermes plugins enable morph
```

Give MORPH the demo tokens in the Hermes profile environment, so the connectors can
authenticate the way they would against any real service:

```bash
cd ~/morph-hermes
./.venv/bin/python -m apps.serve --print-tokens >> ~/.hermes/.env
./.venv/bin/python -m apps.serve &      # leave the three applications running
```

```bash
hermes morph doctor             # configuration, reachability, credentials, storage
hermes morph goal "work through delayed orders and the complaints about them"
hermes morph state
```

```bash
hermes dashboard                # then open http://localhost:9119 from Windows
```

The **MORPH** tab appears in the dashboard navigation. This is the one check the
cloud build could not run — a sandbox refused to execute the Hermes web app's npm
install scripts — so it is worth doing once before you record.

Inside a Hermes session, the agent reaches MORPH on its own:

> *Use MORPH to build me a workspace for delayed orders and the complaints about
> them, then propose flagging the worst one for review.*

It will call `morph_discover`, `morph_plan_workspace` and `morph_propose_action`,
then tell you something is waiting — because proposing is the only thing it can do.

---

## 5. The GitHub connector, against your own repository

```bash
export MORPH_GITHUB_TOKEN="$(gh auth token)"     # or a PAT with 'repo' scope
./.venv/bin/python scripts/check_github_connector.py --repo YOUR-USER/YOUR-REPO --propose
```

Read-only: it introspects the schema, reads issues, derives versions from
`updated_at`, runs the change feed twice, composes a workspace, and builds (then
rejects) a proposal so you can see the before/after without writing anything.

Use a repository that **has open issues** — on an empty one it correctly reports
zero and there is nothing to look at.

To wire it in permanently, add it to your MORPH configuration:

```bash
mkdir -p ~/.hermes/plugin-data/morph
cp config/connectors.github.example.json ~/.hermes/plugin-data/morph/config.json
# edit options.repo, then:
hermes morph doctor
```

---

## 6. Record the demo

```bash
scripts/make_video.sh            # → .media/morph-demo.mp4
```

Needs `ffmpeg` and the Playwright Chromium from step 1. See `docs/DEMO_VIDEO.md`
for the shot list and what to say over it.

---

## 7. Submit

```bash
git status                      # the tree must be clean: the SHA is what gets cloned
./.venv/bin/python scripts/prepare_catalog_entry.py --write
```

It stamps the current commit, pins the screenshot URLs to that same SHA, and refuses
to stamp a commit you have not pushed — because the catalog CI clones your repository
at that SHA and a local-only commit fails there, not here.

Then:

1. Fork `NousResearch/hermes-agent`, add the output as `plugin-catalog/morph.yaml`,
   open the PR. Put the disclosure lines from the entry's `description` in the PR body
   and attach the screenshots from `docs/assets/`.
2. Post the demo video on X tagging **@NousResearch** (`docs/DEMO_VIDEO.md`).
3. Submit both links: **form.typeform.com/to/FwJLvOln**

Deadline is **5PM EST on 11 November**.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `port 8811 is already in use` | a previous run is still up | `pkill -f apps.serve` |
| Playwright cannot launch Chromium | missing Linux libraries | `npx playwright install --with-deps chromium` |
| `hermes: command not found` after install | PATH not reloaded | `source ~/.bashrc` |
| Connector says it needs a credential | token not in the profile environment | `python -m apps.serve --print-tokens >> ~/.hermes/.env` |
| `localhost` refuses from Windows | service bound to a container address | bind `127.0.0.1` (the default here) |
| Tests are slow | repository under `/mnt/c` | move it to `~` |
| apt: `nodejs : Conflicts: npm` | asked for `nodejs npm` together | drop `npm`; NodeSource's `nodejs` already includes it |
| `does not appear to be a Python project` | the clone is on the old `main` | `git fetch origin && git checkout main && git pull` |
| `No module named 'fastapi'` | `pip install -e ".[dev]"` has not run in this venv | run it; every entry point now says so instead of showing a traceback |
| `pytest` errors mention `/usr/lib/python3.x` | a system `pytest` was found on `PATH` | use `make test`, or `./.venv/bin/python -m pytest` |
| `error: externally-managed-environment` | the venv is not active in this shell (no `(.venv)` in the prompt) | `make install` — it never needs activation |
| A `make` target reinstalls every time | `pyproject.toml` is newer than `.venv/.installed` | expected once after a `git pull` that changed it; otherwise `make reinstall` |
