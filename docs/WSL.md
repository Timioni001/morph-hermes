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

> **Check free disk first.** Hermes clones its own repository, fetches a
> managed Python with `uv` and builds its products — several gigabytes. WSL2
> stores all of that in a virtual disk on your Windows drive, so a full `C:`
> stops the install part-way. `df -h /` before you start, and see
> *Reclaiming disk space* below if it is tight.

```bash
ln -s ~/morph-hermes ~/.hermes/plugins/morph
hermes plugins doctor ~/.hermes/plugins/morph --ci
hermes plugins validate ~/.hermes/plugins/morph
hermes plugins enable morph
```

MORPH declares `dependencies = []`, so `--install-deps` has nothing to install
for it. `make validate-ci` adds the flag if you want exactly what catalog CI runs.

Then bring the applications up. One command starts the ones MORPH ships with and
writes their bearer tokens into the Hermes profile environment, so the connectors
authenticate the way they would against any real service:

```bash
hermes morph up
```

It only starts what is bundled with MORPH and configured on loopback; anything
else — a SaaS API, a service another team runs — it reports and leaves alone. Run
it again and it says `already up` rather than starting a second copy. `--detach`
returns instead of holding the terminal.

Inside Hermes it asks Hermes to store the generated tokens, so a dashboard that is
already running picks them up. Run outside Hermes it stores nothing and prints the
`export` lines instead — a plugin is not allowed to write to Hermes's credential
store directly.

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

## Reclaiming disk space

Check `df -h /` before assuming this is your problem. A WSL instance that dies
mid-build is more often out of **memory** than out of disk — the VM is capped at
roughly half your Windows RAM and terminates instead of reporting an error, which
looks identical from the terminal. Raise `memory=` in `C:\Users\<you>\.wslconfig`
and `wsl --shutdown` to apply.

If `df` does show the disk full: deleting files inside WSL does **not** give the
space back to Windows. The virtual disk only ever grows, so after a cleanup
Windows still sees it as full. You have to shrink it too, and that is the step
people miss.

First, from inside WSL, find what is large:

```bash
df -h / && du -xh --max-depth=1 ~ 2>/dev/null | sort -h | tail -15
```

The usual consumers, largest first — check each exists before removing it:

```bash
du -sh ~/.hermes ~/.cache/uv ~/.cache/pip ~/.cache/ms-playwright ~/.npm 2>/dev/null
```

```bash
rm -rf ~/.cache/uv ~/.cache/pip ~/.npm/_cacache
sudo apt clean
```

Hermes keeps its own store under `~/.hermes`, and manages it itself — prefer its
collector over deleting by hand:

```bash
hermes pm gc
```

`~/.hermes/cache/scratch` holds leftover build scratch and can grow to several
gigabytes; it is rebuildable.

MORPH's own footprint is about 75 MB including its virtual environment, so it
is not worth touching. `make clean` reclaims roughly 50 MB if you want it back.

Then shrink the virtual disk. From **PowerShell on Windows**:

```powershell
wsl --shutdown
```

```powershell
wsl --manage Ubuntu --set-sparse true
```

That makes the disk release freed space automatically from then on, which is
the one-time fix. (Substitute your distribution's name from `wsl --list`. On
WSL older than 2.0 the flag does not exist — update with `wsl --update`, or
compact manually with `diskpart`.)

If `C:` itself is nearly full, no amount of cleanup inside WSL will help; free
space on Windows first.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `port 8811 is already in use` | a previous run is still up | `pkill -f 'uvicorn apps\.'` |
| `HTTP Error 401` from the demo | stale applications from an earlier run, holding the ports with the old tokens | same: `pkill -f 'uvicorn apps\.'`, then re-run |
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
| WSL shuts down part-way through a build | usually **memory**, not disk — WSL2 caps itself at about half your Windows RAM and the VM dies rather than the command failing | check `free -h`, then raise `memory=` in `C:\Users\<you>\.wslconfig` and `wsl --shutdown` |
| …and genuinely no space (`df -h /` confirms it) | the virtual disk filled your Windows drive | see *Reclaiming disk space* above; freeing space inside WSL alone is not enough |
| A `make` target reinstalls every time | `pyproject.toml` is newer than `.venv/.installed` | expected once after a `git pull` that changed it; otherwise `make reinstall` |
