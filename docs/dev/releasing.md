# Releasing

## Branches and versions

`develop` is the default and PR branch; `main` is the release branch. Work on a feature
branch named after the GitHub issue.

The version lives in `VERSION`. `make update-version` copies it into `pyproject.toml`
(and `make build` does that first). Add a section for it at the top of `CHANGELOG.rst`,
one bullet per user-visible change with the issue number where there is one.

The packages that depend on nyx-bridge (`nyx-bridge-maya`, `nyx-bridge-blender`, Nyx
itself) pin a **git tag** of it, so tag every release `vX.Y.Z` and move the pins. The tags up to `v1.0.0` were built when the
distribution was still called `nyx-bridge`: a dependent that moves to a newer tag has to
rename the dependency to `sowwic-nyx-bridge` in its `pyproject.toml` (and in
`[tool.uv.sources]`) at the same time.

## Release

```bash
make release VERSION=1.2.3 [RELEASE_TITLE="..."]
```

Runs the release script of `sowwic/gh-workflows`: it tags `main`, pushes, and merges back
into `develop`. The tag triggers `.github/workflows/release.yml`, which creates the GitHub
release from the changelog section and attaches `dist/*`.

## Publishing to PyPI

The distribution is called `sowwic-nyx-bridge` and imports as `nyx_bridge`. The targets come from
the `general/pypi` module of [sowwic/makefiles](https://github.com/sowwic/makefiles):

```bash
make pypi-publish-dev     # build and upload to TestPyPI
make pypi-publish         # build and upload to PyPI
```

Both run `clean` and `build` first, so only a fresh build is uploaded. `uv publish` reads
the token from `UV_PUBLISH_TOKEN`, so put it in the environment for the run, not in a file:

```bash
UV_PUBLISH_TOKEN=pypi-... make pypi-publish
```

| | PyPI | TestPyPI |
| --- | --- | --- |
| Account | pypi.org | test.pypi.org (separate account and tokens) |
| Token page | `pypi.org/manage/account/token/` | `test.pypi.org/manage/account/token/` |

The first upload needs an **account-wide** token, since a token for one project can only be
made once the project exists. After that, replace it with a project-scoped one.

A published version can't be replaced. To fix a bad release, bump `VERSION` and publish
again; to hide one, yank it on pypi.org (there is no API or make target for it).

Before the first upload, check the build and the page:

```bash
make build
uvx twine check dist/*
make pypi-publish-dev
pip install --index-url https://test.pypi.org/simple/ sowwic-nyx-bridge
```

## CI

Workflows in `.github/workflows/` are thin callers of the shared ones in
`sowwic/gh-workflows`: tests (Python matrix on Linux and Windows, then macOS), lint, docs
deployment to GitHub Pages and the release. Dependabot opens weekly update PRs against
`develop` for the lockfile and the Actions versions.
