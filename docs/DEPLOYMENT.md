# Deployment

Azure Container Apps deployment for **Emails, Please**.

← back to [README](../README.md)

---

## Azure deployment

```powershell
Copy-Item deploy\.env.example deploy\.env    # then fill it in
```

```powershell
.\deploy\azure.ps1
```

```bash
./deploy/azure.sh                            # bash / CI equivalent
```

The script builds both images, pushes them to ACR, and creates or updates
the two container apps. `deploy/.env` is gitignored; an exported environment
variable or an explicit parameter beats the file, and anything required but
missing stops the run with a message naming it.

**Free Trial note.** Images are built **locally with `docker build`**, not
with `az acr build`. Free Trial subscriptions cannot run ACR Tasks, so the
server-side build fails with:

```
(TasksOperationsNotAllowed) The requested operation is not allowed
as your subscription is a Free Trial subscription.
```

Docker Desktop therefore has to be running. The push still authenticates via
`az acr login`, which puts a short-lived AAD token in the local docker
credential store — no registry password is stored anywhere.

Tags are UTC timestamps, never reused. `containerapp update --image` with a
tag the app is already on is a no-op — it reports success while still
serving the old image.

```powershell
.\deploy\azure.ps1 -WhatIf                   # print the plan, change nothing
```

---

## Environment variables

Deployment target — `deploy/.env`, see `deploy/.env.example`:

| Variable | Required | |
|---|---|---|
| `RESOURCE_GROUP` | yes | Resource group holding the registry and environment |
| `ACR_NAME` | yes | Registry **name only**, not the login server |
| `ACA_ENV` | yes | Container Apps environment |
| `PG_CONN` | no | Postgres connection string; stored as a Container Apps secret |
| `FIREBASE_PROJECT_ID` | no | Enables token verification on writes |

Application — `.env`, see `.env.example`:

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | `sqlite+pysqlite:///./sdoc.db` | **The `+psycopg` suffix is required** for Postgres: a bare `postgresql://` resolves to psycopg2, which is not installed |
| `DATA_SOURCE` | `../data` | Where the inbox lives |
| `FIREBASE_PROJECT_ID` | unset | Unset means auth is off and writes are open |
| `CORS_ORIGINS` | localhost dev ports | Unused in the deployed setup — the proxy makes it same-origin |

Example shape, no real values:

```
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require
```

Tables are created automatically at startup (`create_all`), so an empty
database needs no migration step.

> **Durability:** without `PG_CONN` the API runs on SQLite *inside the
> container*, and every run and review is lost on restart or scale-to-zero.
> The deploy prints which mode it is using.
