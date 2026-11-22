# Deploying ORCA

Two ways to put ORCA online:

| | Vercel (free) | Fly.io (paid, about $10-15 a month) |
|---|---|---|
| What runs | The home page and the control room as static files; a lighter JavaScript version of the dispatcher runs in each visitor's browser on a 24,000-trip sample of the real trips | The Python server: one process per city, the learned ETA and demand models, the full trip replay, live streaming to every viewer |
| Cost | Free (Hobby plan, personal non-commercial use), no card | One always-on 2 GB machine and a 1 GB volume |
| Sleeps | Never | Never, once a card is on the account (a trial account stops machines after 5 minutes) |
| Not available | The server-only what-ifs (200 drivers log off, shared rides, electric fleet), the live API, `/metrics` | Nothing |

## Vercel (free)

Live at **https://orca-tau-ten.vercel.app**.

[`tools/build_site.mjs`](../tools/build_site.mjs) assembles `site/` at the same paths the server uses (`/`, `/app`,
`/app/data/...`, `/media/...`) and gives it its own `vercel.json` ([`deploy/vercel-site.json`](../deploy/vercel-site.json): the
`/app` rewrite, the server's security headers and caching). There is no `/api`, so the control room runs its
in-browser engine: the same scoring rules, tuned weights and street speeds as the server for each city, with simpler
models (a grid demand forecast instead of the learned per-zone models, no learned ETA model) on a 24,000-trip sample.
CI builds the same site on every pull request.

Vercel deploys the `site/` folder as it is, with nothing built on Vercel's side. Deploying the repository root instead
does not work: Vercel finds the `Dockerfile` there and sets the project up as a container.

```bash
npx vercel login                                   # once: approve in the browser (free Hobby plan, no card)
npx vercel link --cwd site --project orca --yes    # once per clone, after a first `make site`
make vercel                                        # builds site/ and deploys it to production
```

`make site` alone builds the folder; serve it locally with `python -m http.server --directory site` to try it.
`site/.vercel` keeps the project link across rebuilds.

The Vercel project is connected to the GitHub repository, but the root [`vercel.json`](../vercel.json) turns off
Vercel's own deploys on push (`git.deploymentEnabled: false`): they would build from the repository root, which does
not produce the site. Production changes only through `make vercel`.

# Fly.io (the full server)

ORCA runs as one Docker container: `orca.router` on port 8000 with one supervised process per city
(`ORCA_CITIES`, Manhattan and Chicago by default, about 1.4 GB together). [`fly.toml`](../fly.toml) asks for a
2 GB machine in Newark, HTTPS, a health check on `/api/health`, and a 1 GB volume for the trip ledger. One
machine stays on, so visitors never wait for a cold start.

## One-time setup (about 10 minutes)

Fly needs an account with a payment method. **Without one, Fly runs the account as a trial and stops every
machine after 5 minutes**; the next visitor then waits through a cold start. Add a card at
https://fly.io/trial before deploying. Every command runs from the repository root.

1. Install the Fly command line and sign in (a browser window opens):

   ```powershell
   iwr https://fly.io/install.ps1 -useb | iex
   fly auth login
   ```

2. Create the app. App names are global: if `orca-dispatch` is taken, pick another name and put it in the
   `app = "..."` line of `fly.toml`.

   ```powershell
   fly apps create orca-dispatch
   ```

3. Create the ledger volume, in the same region as the machine:

   ```powershell
   fly volumes create orca_ledger --region ewr --size 1 --yes -a orca-dispatch
   ```

4. Set the operator token. It unlocks the control room's operator controls (speed, pause, demand, fleet):
   open `https://<app>.fly.dev/app?token=<token>` once. Visitors without it can watch but not steer.

   ```powershell
   fly secrets set ORCA_ADMIN_TOKEN=$(-join ((48..57) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ })) -a orca-dispatch
   ```

5. Let GitHub deploy: create a deploy token and store it as the repository secret `FLY_API_TOKEN`.

   ```powershell
   fly tokens create deploy -a orca-dispatch | gh secret set FLY_API_TOKEN
   ```

## Deploy

GitHub: **Actions > Deploy to Fly.io > Run workflow**, or from a terminal:

```powershell
gh workflow run deploy.yml
```

The workflow builds the image on GitHub's runner, pushes it to Fly's registry, deploys it and checks
`https://<app>.fly.dev/api/health`. The first build takes about 45 minutes, because it downloads and prepares a
month of real trips (Chicago's open-data API alone takes about 35 minutes). Later builds reuse that download
from the build cache.

Deploying from your own machine with `fly deploy` also works, but it builds on Fly's builder, which has no cache
of the trip data.

## After deploying

| | |
|---|---|
| Home page | `https://<app>.fly.dev/` |
| Control room | `https://<app>.fly.dev/app` |
| Every city process | `https://<app>.fly.dev/api/cities` |
| Logs | `fly logs -a orca-dispatch` |
| Metrics (Prometheus) | `https://<app>.fly.dev/metrics` |

**Adding Cambridge:** set `ORCA_CITIES = "manhattan chicago cambridge"` and `memory = "4gb"` in `fly.toml`.

**Cold starts:** compiled Numba kernels are cached on the ledger volume (`numba-cache/`), so only the first boot
after a deploy of new kernel code compiles them.

**Cost:** one always-on 2 GB shared-CPU machine and a 1 GB volume. See Fly's pricing page for current rates.
To pay only while someone is visiting, set `min_machines_running = 0`. The first visitor after a quiet
period then waits a minute or two while the cities warm up.
