# Deploying to Fly.io

ORCA runs as one Docker container: `orca.router` on port 8000 with one supervised process per city
(`ORCA_CITIES`, Manhattan and Chicago by default, about 1.4 GB together). [`fly.toml`](../fly.toml) asks for a
2 GB machine in Newark, HTTPS, a health check on `/api/health`, and a 1 GB volume for the trip ledger. One
machine stays on, so visitors never wait for a cold start.

## One-time setup (about 10 minutes)

Fly needs an account with a payment method. Every command runs from the repository root.

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

**Cost:** one always-on 2 GB shared-CPU machine and a 1 GB volume. See Fly's pricing page for current rates.
To pay only while someone is visiting, set `min_machines_running = 0`. The first visitor after a quiet
period then waits a minute or two while the cities warm up.
