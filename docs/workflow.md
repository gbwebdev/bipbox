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

SemVer tags `vX.Y.Z` on `main`, **one version for the whole repository**. The
server and the device firmware are released together because they share a
protocol; versioning them separately would invite combinations nobody tested.

- `MAJOR` — breaking protocol or data-model change
- `MINOR` — new features, backwards compatible
- `PATCH` — fixes only

A tag triggers a GitHub Release carrying the generated changelog and, once
phase 6 exists, the built `.img.xz` as an asset. The device's "check for update"
button (Q36) reads the latest release from the public API — there is no
auto-update.

Pre-1.0 the usual caveat applies: minor versions may break things, and will say
so in the release notes.

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
