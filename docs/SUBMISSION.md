# Submitting MORPH

Three things go out: the catalog PR, the demo video, and the hackathon form.

## 1. The catalog PR

The rules are in Hermes's own docs:
[Submitting to the plugin catalog](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission)
and `plugin-catalog/README.md` in `NousResearch/hermes-agent`. In short: one YAML file,
a full 40-character commit SHA, a public repository, submitted by the repository's
owner, with disclosures in the PR description.

1. **The repository must be public**, and the commit you pin must be on it. Merge the
   work to `main` first so the pinned commit is also what the repository front page
   shows.
2. Stamp the entry and the PR description with that commit:

   ```bash
   python scripts/prepare_catalog_entry.py --write
   ```

   This writes `plugin-catalog-morph.yaml` and `plugin-catalog-pr-body.md` (both
   git-ignored). It refuses a commit that is not pushed.
3. Fork `NousResearch/hermes-agent`, add the first file as `plugin-catalog/morph.yaml`
   — that one file and nothing else — and open a PR with the second as its description.
   Suggested title: `feat(catalog): add morph`.
4. Catalog CI clones the repository at the pinned SHA and runs
   `hermes plugins validate --install-deps`. `make validate-ci` runs the same locally.
5. Any later change to the plugin means a new commit, so re-run step 2 and push the
   new SHA to the same PR.

## 2. The video

`docs/DEMO_VIDEO.md` has the shot list. It needs a scratch GitHub repository with a
few open issues and a `GITHUB_TOKEN` in your Hermes environment.

## 3. The form and the post

Post the video on X tagging @NousResearch (a caption is at the end of
`docs/DEMO_VIDEO.md`), then submit the repository, the catalog PR link and the post at
<https://form.typeform.com/to/FwJLvOln>.
