# Bipbox — development workflow

## Branches

`main` is always deployable and is **protected**: no direct pushes, changes land
only through a reviewed pull request.

| Prefix | For |
|---|---|
| `feat/<slug>` | a new capability |
| `fix/<slug>` | a bug fix |
| `docs/<slug>` | documentation only |
| `chore/<slug>` | tooling, CI, dependencies |
| `refactor/<slug>` | no behaviour change |
| `spike/<slug>` | throwaway experiment, never merged |

One branch per unit of reviewable work. Phase-sized branches (`feat/telex`) are
too big to review; prefer `feat/telex-printer-detection`.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/), because the
changelog and the release notes are generated from them:

```
<type>(<scope>): <summary in the imperative>

<body: why, not what>
```

Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`, `build`, `ci`.
Scopes: `server`, `device`, `console`, `image`, `voip`, `telex`, `telegraphy`,
`cad`, `docs`.

```
feat(device): drive the status LEDs over SPI

The 74HC595 is latched from SPI0 CE1, so a transfer's trailing CS edge
does the latching and no extra GPIO is needed.
```

Breaking changes carry a `!` (`feat(server)!: …`) and a `BREAKING CHANGE:`
footer. For this project "breaking" primarily means the device↔server protocol
(see Compatibility below).

## Pull requests

- Squash merge, so `main` stays linear and one PR is one commit. The squash
  subject must itself be a valid Conventional Commit.
- CI must pass. No merging red.
- Fill in the template: what, why, how it was tested, and explicitly whether
  real hardware was involved — for this project "tested on a Pi Zero W with the
  HAT" and "tested on a laptop" are very different claims.
- Draft PRs are welcome early; mark ready when CI is green.

## Releases

Releases are automated by **release-please**, driven entirely by the commit
messages above — which is the practical reason Conventional Commits are
mandatory rather than merely tidy.

SemVer tags `vX.Y.Z`, **one version for the whole repository** (`version.txt` is
the single source of truth). The server and the device firmware release together
because they share a protocol; versioning them separately would invite
combinations nobody tested.

### How it works

1. Merge PRs into `main` as usual.
2. The `Release` workflow opens and keeps updating a **release PR** —
   "chore(main): release X.Y.Z" — which bumps `version.txt` and writes
   `CHANGELOG.md`.
3. Merging that PR tags the release and publishes a GitHub Release with the
   generated notes.

Nothing is released until that PR is merged, so the release PR is also the
moment to read the changelog and decide whether it is time.

### Version bumps

Pre-1.0 is configured with `bump-minor-pre-major`, so:

| Commit | Bump while < 1.0 |
|---|---|
| `fix:` | patch |
| `feat:` | minor |
| `feat!:` / `BREAKING CHANGE:` | **minor**, not major |

That matches reality for a project at this stage: minor versions may break
things and will say so in the notes. After 1.0 the normal SemVer rules apply.

The first release is pinned to **0.1.0** via `initial-version`. Without it
release-please treats an initial release as `1.0.0`, which would claim a
stability this project has not earned yet.

### Repository prerequisite

Release-please opens its PR as GitHub Actions, which is forbidden by default.
If you fork this repo, enable **Settings → Actions → General → Workflow
permissions → "Allow GitHub Actions to create and approve pull requests"**, or
the `Release` workflow fails with *"GitHub Actions is not permitted to create or
approve pull requests"* after having already pushed its branch.

### The GITHUB_TOKEN caveat

GitHub deliberately does not run workflows for events raised by
`GITHUB_TOKEN`, so **CI does not run on the release PR** and its required
`lint`/`test` checks sit pending forever. Two ways out:

- **Merge it with admin bypass** (`enforce_admins` is off, so this works today).
  Safe enough: the release PR only touches `version.txt` and `CHANGELOG.md`, and
  CI already passed on every commit it describes.
- **Add a `RELEASE_PLEASE_TOKEN` secret** — a fine-grained PAT with
  *contents: write* and *pull requests: write* on this repo. The workflow picks
  it up automatically and the release PR then gets real CI runs.

### Release assets

Once phase 6 exists, the built `.img.xz` is attached to the release, gated on
release-please's `release_created` output. The device's "check for update"
button (Q36) reads the latest release from the public API — there is no
auto-update.

## Compatibility

The device↔server `hello` handshake carries a `protocol_version`. A box whose
protocol version is older than the server's minimum is told to update rather
than being allowed to fail in some obscure way later. This is what makes
"release them together" safe in practice, given boxes update by hand.

## CI

On every PR and every push to `main`:

- `ruff check` and `ruff format --check`
- `pytest`

Hardware-dependent code (SPI, GPIO, ALSA, ESC/POS) cannot be covered in CI, so
it is isolated behind interfaces with a fake-hardware implementation
(architecture.md §12, phase 1) and *that* is what CI exercises. Anything that
can only be verified on the bench says so in its PR.
