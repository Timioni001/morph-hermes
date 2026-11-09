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

Node 20+ is needed for the browser suite, and Ubuntu's packaged npm is usually too
old. `nvm` avoids both a root install and a piped-to-shell setup script:

```bash
sudo apt install -y nodejs npm            # only to bootstrap; nvm replaces it
git clone --depth 1 https://github.com/nvm-sh/nvm.git ~/.nvm
. ~/.nvm/nvm.sh && nvm install 20 && nvm use 20
node -v                                   # expect v20.x or newer
```

```bash
git clone https://github.com/timioni001/morph-hermes ~/morph-hermes
cd ~/morph-hermes
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

```bash
# Chromium plus the Linux libraries it needs under WSL.
npm install --no-save playwright@1.49.1
npx playwright install --with-deps chromium
```

---

## 2. Prove it works, in four commands

```bash
pytest
```

```bash
scripts/e2e.sh
```

```bash
python -m demo.run_demo
```

```bash
node scripts/build_dashboard.mjs --check
```

The first three take about a minute together. `demo/run_demo.py` is the one to watch:
it exits non-zero if any claim in it fails.

---

## 3. See it

```bash
python -m demo.run_demo --serve
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
python -m apps.serve --print-tokens >> ~/.hermes/.env
python -m apps.serve &          # leave the three applications running
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
python scripts/check_github_connector.py --repo YOUR-USER/YOUR-REPO --propose
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

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `port 8811 is already in use` | a previous run is still up | `pkill -f apps.serve` |
| Playwright cannot launch Chromium | missing Linux libraries | `npx playwright install --with-deps chromium` |
| `hermes: command not found` after install | PATH not reloaded | `source ~/.bashrc` |
| Connector says it needs a credential | token not in the profile environment | `python -m apps.serve --print-tokens >> ~/.hermes/.env` |
| `localhost` refuses from Windows | service bound to a container address | bind `127.0.0.1` (the default here) |
| Tests are slow | repository under `/mnt/c` | move it to `~` |
