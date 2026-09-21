<#
.SYNOPSIS
    Build both images locally with docker, push them to ACR, and deploy
    them to Azure Container Apps.

.DESCRIPTION
    PowerShell equivalent of deploy/azure.sh, for Windows PowerShell 5.1.

    Images are built LOCALLY rather than with `az acr build`. Free Trial
    subscriptions cannot run ACR Tasks, so the server-side build fails with:

        (TasksOperationsNotAllowed) The requested operation is not allowed
        as your subscription is a Free Trial subscription.

    Docker Desktop therefore has to be running. The push still authenticates
    through `az acr login`, which writes a short-lived AAD token into the
    local docker credential store -- no registry password is kept anywhere.

    The target is configuration, not a built-in default. ResourceGroup,
    AcrName and AcaEnv are REQUIRED and the script refuses to run without
    them: shipping a script with someone's real registry hardcoded leaves
    the next person one typo away from deploying into the wrong
    subscription. They resolve in this order, first wins:

        1. an explicit parameter
        2. an environment variable (RESOURCE_GROUP / ACR_NAME / ACA_ENV)
        3. deploy/.env, if it exists -- copy deploy/.env.example

    All three resources must already exist. This script creates only the
    two container apps, and re-deploys them on later runs.

    Without -PgConn the API runs on SQLite INSIDE the container, so every
    run and every review decision is lost when it restarts or scales to
    zero. That is fine for a demo you drive live and wrong for anything
    else; the script prints which mode it is using.

.EXAMPLE
    .\deploy\azure.ps1
    Deploy using deploy/.env, with SQLite (ephemeral).

.EXAMPLE
    .\deploy\azure.ps1 -PgConn "postgresql+psycopg://user:pass@host/db"
    Deploy against a durable Postgres.

.EXAMPLE
    .\deploy\azure.ps1 -Tag rc1
    Deploy a specific tag. Otherwise the tag is a fresh UTC timestamp, which
    is what makes a redeploy actually pull the image it just built.

.EXAMPLE
    .\deploy\azure.ps1 -WhatIf
    Print the commands without running them.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    # Deliberately no defaults. These are resolved in the body, AFTER
    # deploy/.env has been loaded -- PowerShell evaluates parameter
    # defaults at bind time, before any of the script has run, so a
    # default here could never see the file.
    [string]$ResourceGroup,
    [string]$AcrName,
    [string]$AcaEnv,
    [string]$PgConn,
    [string]$FirebaseProject,
    [string]$Tag
)

$ErrorActionPreference = 'Stop'

function Import-DotEnv {
    <#
        Load KEY=VALUE lines into the process environment.

        Existing variables are never overwritten: an exported variable is
        a deliberate act for this one invocation, and a file on disk should
        not silently beat it.
    #>
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return }

    foreach ($line in Get-Content -LiteralPath $Path) {
        $text = $line.Trim()
        if (-not $text -or $text.StartsWith('#')) { continue }
        if ($text.StartsWith('export ')) { $text = $text.Substring(7).Trim() }

        # Split on the FIRST '=' only: connection strings carry their own.
        $split = $text.IndexOf('=')
        if ($split -lt 1) { continue }

        $key   = $text.Substring(0, $split).Trim()
        $value = $text.Substring($split + 1).Trim()
        if ($value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        if (-not [Environment]::GetEnvironmentVariable($key)) {
            # [Environment], not Set-Item: Set-Item supports ShouldProcess,
            # so under -WhatIf it would only *describe* setting the
            # variable. Config would stay unloaded and the dry run would
            # then fail its own required-settings check -- reporting a
            # problem that exists only during the preview.
            [Environment]::SetEnvironmentVariable($key, $value)
        }
    }
}

# Load before resolving anything, so the file can supply what the
# environment does not.
Import-DotEnv (Join-Path $PSScriptRoot '.env')

if (-not $ResourceGroup)   { $ResourceGroup   = $env:RESOURCE_GROUP }
if (-not $AcrName)         { $AcrName         = $env:ACR_NAME }
if (-not $AcaEnv)          { $AcaEnv          = $env:ACA_ENV }
if (-not $PgConn)          { $PgConn          = $env:PG_CONN }
if (-not $FirebaseProject) { $FirebaseProject = $env:FIREBASE_PROJECT_ID }

# Fail before touching Azure, and name everything that is missing rather
# than surfacing them one re-run at a time.
$missing = @()
if (-not $ResourceGroup) { $missing += 'RESOURCE_GROUP (-ResourceGroup)' }
if (-not $AcrName)       { $missing += 'ACR_NAME (-AcrName)' }
if (-not $AcaEnv)        { $missing += 'ACA_ENV (-AcaEnv)' }
if ($missing.Count -gt 0) {
    throw ("Missing required deployment settings:`n  - " +
           ($missing -join "`n  - ") +
           "`n`nSet them in deploy/.env (copy deploy/.env.example), export them, " +
           "or pass them as parameters.")
}

# A login server here instead of a bare name produces
# "myreg.azurecr.io.azurecr.io", which fails at push with an unhelpful
# DNS error. Catch it while the message can still say why.
if ($AcrName -match '\.azurecr\.io') {
    throw ("ACR_NAME must be the registry NAME, not its login server: " +
           "use '$($AcrName -replace '\.azurecr\.io.*$', '')', not '$AcrName'.")
}

function Invoke-Az {
    <#
        Run az and stop on a non-zero exit code.

        az writes progress to stderr even on success, so its exit code --
        not its stderr -- is the only reliable signal. Without this check a
        failed build would sail on and "deploy" the previous image.
    #>
    param([Parameter(Mandatory)][string[]]$Arguments, [string]$Activity)

    if ($Activity) { Write-Host "    $Activity" -ForegroundColor DarkGray }
    Invoke-Native az $Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "az $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Native {
    <#
        Run a native executable without letting its stderr abort the script.

        $ErrorActionPreference = 'Stop' promotes ANYTHING a native command
        writes to stderr into a TERMINATING error. Both az and docker write
        ordinary progress there -- buildkit's very first line is
        "#0 building with ... docker driver" -- so with 'Stop' in force the
        script dies before the build has done any work, reporting a
        NativeCommandError that names no actual failure.

        Exit code is the only trustworthy signal, so suppress the promotion
        for the duration of the call and let the callers check
        $LASTEXITCODE.
    #>
    param([Parameter(Mandatory)][string]$Exe,
          [Parameter(Mandatory)][string[]]$Arguments)

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Invoke-Docker {
    <#
        Run docker and stop on a non-zero exit code.

        Like az, docker writes build progress to stderr on success, so the
        exit code is the only reliable signal. Without this check a failed
        build would fall straight through to `docker push` and the deploy
        would ship whatever was last tagged.
    #>
    param([Parameter(Mandatory)][string[]]$Arguments, [string]$Activity)

    if ($Activity) { Write-Host "    $Activity" -ForegroundColor DarkGray }
    Invoke-Native docker $Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Get-AzValue {
    <# Run az and return its single tsv value, or $null. #>
    param([Parameter(Mandatory)][string[]]$Arguments)

    $value = (Invoke-Native az $Arguments)
    if ($LASTEXITCODE -ne 0) { return $null }
    if ($null -eq $value) { return $null }
    return ($value | Out-String).Trim()
}

function Test-ContainerApp {
    <#
        Existence by listing rather than `show`, which writes an error to
        stderr when the app is absent. Listing returns an empty string, so
        the normal path stays quiet.
    #>
    param([Parameter(Mandatory)][string]$Name)

    $found = Get-AzValue @(
        'containerapp', 'list',
        '--resource-group', $ResourceGroup,
        '--query', "[?name=='$Name'].name",
        '-o', 'tsv'
    )
    return -not [string]::IsNullOrWhiteSpace($found)
}

function Grant-AcrPull {
    <#
        Let the app pull from ACR under its own managed identity, so no
        registry password is stored anywhere.

        The role assignment is best-effort: it fails harmlessly if it
        already exists, or if the signed-in principal cannot assign roles
        (in which case an owner has to grant AcrPull once, by hand).
    #>
    param([Parameter(Mandatory)][string]$AppName)

    $principal = Get-AzValue @(
        'containerapp', 'show', '--name', $AppName,
        '--resource-group', $ResourceGroup,
        '--query', 'identity.principalId', '-o', 'tsv'
    )
    $acrId = Get-AzValue @('acr', 'show', '--name', $AcrName, '--query', 'id', '-o', 'tsv')

    if ($principal -and $acrId) {
        Invoke-Native az @('role', 'assignment', 'create',
                           '--assignee', $principal, '--role', 'AcrPull',
                           '--scope', $acrId, '--output', 'none')
        if ($LASTEXITCODE -ne 0) {
            Write-Host "    note: AcrPull not granted (may already exist, or you lack permission)" -ForegroundColor Yellow
        }
    }

    Invoke-Az @(
        'containerapp', 'registry', 'set', '--name', $AppName,
        '--resource-group', $ResourceGroup,
        '--server', $Registry, '--identity', 'system', '--output', 'none'
    )
}

# --- context -----------------------------------------------------------
# docker build resolves its context and -f path relative to the working
# directory, so the repo root must be current whichever folder the script
# was invoked from.
#
# -LiteralPath throughout: PowerShell treats [ ] as a wildcard character
# class, so a repo checked out under a path like "[!] Problem Statement"
# fails with "cannot find path" on the plain form.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $RepoRoot
try {
    if (-not $Tag) {
        # A UTC timestamp, so every run produces a tag never deployed before.
        #
        # This is not cosmetic. `containerapp update --image` with the tag
        # the app is ALREADY on is a no-op: no new revision, no fresh pull,
        # and the deploy reports success while still serving the old image.
        # A git sha has the same failure whenever you rebuild without
        # committing -- which, while debugging, is most of the time. The sha
        # is appended for traceability but never used on its own.
        $Tag = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')

        # With $ErrorActionPreference = 'Stop', anything a native command
        # writes to stderr becomes a TERMINATING error -- so a plain
        # `git rev-parse` in a non-repo folder aborts the script before the
        # fallback can run. Suppress, and treat empty as "no sha".
        $sha = $null
        try {
            $ErrorActionPreference = 'SilentlyContinue'
            $sha = (& git rev-parse --short HEAD 2>$null | Select-Object -First 1)
        } catch {
            $sha = $null
        } finally {
            $ErrorActionPreference = 'Stop'
        }
        if (-not [string]::IsNullOrWhiteSpace($sha)) {
            $Tag = "$Tag-$($sha.ToString().Trim())"
        }
    }
    $Registry = "$AcrName.azurecr.io"

    Write-Host '==> Target' -ForegroundColor Cyan
    Write-Host "    group=$ResourceGroup  registry=$Registry  env=$AcaEnv  tag=$Tag"
    if ($PgConn) {
        Write-Host '    database: Postgres (durable)'
    } else {
        Write-Host '    database: SQLite in-container -- runs and reviews are lost on restart' -ForegroundColor Yellow
    }

    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        throw 'az CLI not found on PATH. Install the Azure CLI, then run: az login'
    }
    $account = Get-AzValue @('account', 'show', '--query', 'name', '-o', 'tsv')
    if (-not $account) { throw 'Not signed in. Run: az login' }
    Write-Host "    subscription=$account"

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw ('docker not found on PATH. Images are built locally because ' +
               'Free Trial subscriptions cannot run ACR Tasks. Install ' +
               'Docker Desktop, start it, then re-run.')
    }
    # The client exists even when the daemon is down, and that failure
    # otherwise surfaces mid-build as an opaque error. Check it up front.
    if (-not $WhatIfPreference) {
        $ErrorActionPreference = 'SilentlyContinue'
        & docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        $dockerUp = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = 'Stop'
        if (-not $dockerUp) {
            throw 'Docker is installed but its daemon is not responding. Start Docker Desktop and re-run.'
        }
    }

    # --- build ---------------------------------------------------------
    Write-Host '==> Building images locally, pushing to ACR' -ForegroundColor Cyan
    if ($PSCmdlet.ShouldProcess($Registry, "build and push sdoc-api:$Tag and sdoc-web:$Tag")) {
        # Writes a short-lived AAD token into the local docker credential
        # store, so the push needs no registry password.
        Invoke-Az @('acr', 'login', '--name', $AcrName) 'registry login'

        # Root context: the API image carries data/ (see backend/Dockerfile).
        Invoke-Docker @('build', '-t', "$Registry/sdoc-api:$Tag",
                        '-f', 'backend/Dockerfile', '.') 'building api'
        Invoke-Docker @('build', '-t', "$Registry/sdoc-web:$Tag",
                        './frontend') 'building web'

        Invoke-Docker @('push', "$Registry/sdoc-api:$Tag") 'pushing api'
        Invoke-Docker @('push', "$Registry/sdoc-web:$Tag") 'pushing web'
    }

    # --- api -----------------------------------------------------------
    $apiEnv = @('DATA_SOURCE=/data')
    $secretArgs = @()
    if ($PgConn) {
        $secretArgs = @('--secrets', "db-url=$PgConn")
        $apiEnv += 'DATABASE_URL=secretref:db-url'
    }
    if ($FirebaseProject) { $apiEnv += "FIREBASE_PROJECT_ID=$FirebaseProject" }

    Write-Host '==> Deploying API (internal ingress)' -ForegroundColor Cyan
    if ($PSCmdlet.ShouldProcess('sdoc-api', 'deploy')) {
        if (Test-ContainerApp 'sdoc-api') {
            # The secret has to exist BEFORE anything references it. On the
            # create path --secrets does that in the same call; update has
            # no such argument, so an app first deployed without -PgConn has
            # no db-url secret and `--set-env-vars
            # DATABASE_URL=secretref:db-url` is rejected. `secret set` is
            # create-or-update, so re-running it is harmless.
            if ($PgConn) {
                Invoke-Az @('containerapp', 'secret', 'set', '--name', 'sdoc-api',
                            '--resource-group', $ResourceGroup,
                            '--secrets', "db-url=$PgConn",
                            '--output', 'none') 'storing db-url secret'
            } else {
                # --set-env-vars only adds and updates, never removes, so an
                # app already on Postgres stays on Postgres. Say so, rather
                # than let the "SQLite" banner above mislead.
                # Query .name, not .value: a secret-backed variable carries
                # its value in `secretRef` and reports value="", so asking
                # for the value says "unset" for exactly the Postgres
                # deployments this warning exists to describe.
                $existingDb = Get-AzValue @(
                    'containerapp', 'show', '--name', 'sdoc-api',
                    '--resource-group', $ResourceGroup,
                    '--query', "properties.template.containers[0].env[?name=='DATABASE_URL'].name",
                    '-o', 'tsv'
                )
                if (-not [string]::IsNullOrWhiteSpace($existingDb)) {
                    Write-Host ("    note: app already has DATABASE_URL and keeps it " +
                                "(-PgConn not supplied, so it was left alone)") -ForegroundColor Yellow
                }
            }
            Invoke-Az @('containerapp', 'update', '--name', 'sdoc-api',
                        '--resource-group', $ResourceGroup,
                        '--image', "$Registry/sdoc-api:$Tag", '--output', 'none') 'updating image'
            $refresh = @('containerapp', 'update', '--name', 'sdoc-api',
                         '--resource-group', $ResourceGroup,
                         '--set-env-vars') + $apiEnv + @('--output', 'none')
            Invoke-Az $refresh 'refreshing environment'
        } else {
            $create = @(
                'containerapp', 'create',
                '--name', 'sdoc-api',
                '--resource-group', $ResourceGroup,
                '--environment', $AcaEnv,
                '--image', "$Registry/sdoc-api:$Tag",
                '--system-assigned',
                '--target-port', '8000', '--ingress', 'internal',
                # min-replicas 1: with SQLite, scaling to zero destroys all
                # state. Safe to lower only once PgConn is supplied.
                '--min-replicas', '1', '--max-replicas', '3',
                '--cpu', '1', '--memory', '2Gi'
            ) + $secretArgs + @('--env-vars') + $apiEnv + @('--output', 'none')
            Invoke-Az $create 'creating app'
            Grant-AcrPull 'sdoc-api'
        }
    }

    # Under -WhatIf nothing was created, so looking the app up would only
    # print a ResourceNotFound the reader has to learn to ignore.
    $apiFqdn = '<api-fqdn>'
    if (-not $WhatIfPreference) {
        $apiFqdn = Get-AzValue @(
            'containerapp', 'show', '--name', 'sdoc-api',
            '--resource-group', $ResourceGroup,
            '--query', 'properties.configuration.ingress.fqdn', '-o', 'tsv'
        )
        # Deploying web against an empty FQDN is how sdoc-web ended up with
        # no BACKEND_URL and crash-looped. Refuse rather than ship it.
        if ([string]::IsNullOrWhiteSpace($apiFqdn)) {
            throw ("sdoc-api has no ingress FQDN. The web app proxies /api to it, " +
                   "so deploying now would leave BACKEND_URL empty. Check sdoc-api " +
                   "is running with internal ingress enabled.")
        }
        Write-Host "    api fqdn=$apiFqdn" -ForegroundColor DarkGray
    }

    # --- web -----------------------------------------------------------
    Write-Host '==> Deploying web (external ingress, proxying /api to the API)' -ForegroundColor Cyan
    if ($PSCmdlet.ShouldProcess('sdoc-web', 'deploy')) {
        if (Test-ContainerApp 'sdoc-web') {
            Invoke-Az @('containerapp', 'update', '--name', 'sdoc-web',
                        '--resource-group', $ResourceGroup,
                        '--image', "$Registry/sdoc-web:$Tag", '--output', 'none') 'updating image'
            # Env vars are NOT carried by `update --image`. Without this an
            # app created with a bad or missing BACKEND_URL can never be
            # repaired by re-running the deploy -- which is exactly how the
            # first crash-loop survived several attempts.
            Invoke-Az @('containerapp', 'update', '--name', 'sdoc-web',
                        '--resource-group', $ResourceGroup,
                        '--set-env-vars', "BACKEND_URL=https://$apiFqdn",
                        '--output', 'none') 'refreshing BACKEND_URL'
        } else {
            Invoke-Az @(
                'containerapp', 'create',
                '--name', 'sdoc-web',
                '--resource-group', $ResourceGroup,
                '--environment', $AcaEnv,
                '--image', "$Registry/sdoc-web:$Tag",
                '--system-assigned',
                '--target-port', '80', '--ingress', 'external',
                '--min-replicas', '1', '--max-replicas', '2',
                '--cpu', '0.5', '--memory', '1Gi',
                # NGINX_ENVSUBST_FILTER is an image default now; setting
                # it here would override it and drop the resolver
                # substitution, which stops nginx starting.
                '--env-vars', "BACKEND_URL=https://$apiFqdn",
                '--output', 'none'
            ) 'creating app'
            Grant-AcrPull 'sdoc-web'
        }
    }

    if ($WhatIfPreference) {
        Write-Host ''
        Write-Host '==> Dry run: nothing was built or deployed.' -ForegroundColor Green
        Write-Host '    Re-run without -WhatIf to apply.'
        return
    }

    $webFqdn = Get-AzValue @(
        'containerapp', 'show', '--name', 'sdoc-web',
        '--resource-group', $ResourceGroup,
        '--query', 'properties.configuration.ingress.fqdn', '-o', 'tsv'
    )
    if ([string]::IsNullOrWhiteSpace($webFqdn)) {
        throw 'sdoc-web deployed but has no ingress FQDN; check the app in the portal.'
    }

    Write-Host ''
    Write-Host "==> Live at https://$webFqdn" -ForegroundColor Green
    Write-Host '    Health check:'
    Write-Host "      Invoke-RestMethod https://$webFqdn/health"
    Write-Host '    Trigger the first pipeline run from the UI, or:'
    Write-Host "      Invoke-RestMethod -Method Post https://$webFqdn/api/runs"
}
finally {
    Pop-Location
}
