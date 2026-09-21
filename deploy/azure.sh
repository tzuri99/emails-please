#!/usr/bin/env bash
# Build both images in ACR and deploy them to Container Apps.
#
# The target is configuration, not a built-in default. RESOURCE_GROUP,
# ACR_NAME and ACA_ENV are REQUIRED: a script shipped with someone's real
# registry hardcoded leaves the next person one typo away from deploying
# into the wrong subscription. They resolve in this order, first wins:
#
#   1. an exported environment variable
#   2. deploy/.env, if it exists -- copy deploy/.env.example
#
# All three resources must already exist; this script creates only the two
# container apps.
#
#   ./deploy/azure.sh                 # settings from deploy/.env
#   PG_CONN=postgresql+psycopg://...  ./deploy/azure.sh
#
# Without PG_CONN the API runs on SQLite inside the container: review
# decisions are lost when it restarts. That is fine for a demo and wrong for
# anything else, so the script says which mode it is using.
set -euo pipefail

# --- configuration -----------------------------------------------------
# Load deploy/.env without clobbering anything already exported: an
# exported variable is a deliberate act for this one invocation, and a file
# on disk should not silently beat it.
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.env"
if [ -f "$ENV_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"          # strip leading space
    case "$line" in ''|'#'*) continue ;; esac
    line="${line#export }"
    case "$line" in *=*) ;; *) continue ;; esac
    key="${line%%=*}"                                 # first '=' only:
    value="${line#*=}"                                # conn strings have more
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    case "$value" in
      \"*\") value="${value%\"}"; value="${value#\"}" ;;
      \'*\') value="${value%\'}"; value="${value#\'}" ;;
    esac
    if [ -z "${!key:-}" ]; then export "$key=$value"; fi
  done < "$ENV_FILE"
fi

RESOURCE_GROUP="${RESOURCE_GROUP:-}"
ACR_NAME="${ACR_NAME:-}"
ACA_ENV="${ACA_ENV:-}"
PG_CONN="${PG_CONN:-}"
FIREBASE_PROJECT_ID="${FIREBASE_PROJECT_ID:-}"

# Fail before touching Azure, and name everything missing at once rather
# than surfacing them one re-run at a time.
missing=()
[ -n "$RESOURCE_GROUP" ] || missing+=("RESOURCE_GROUP")
[ -n "$ACR_NAME" ]       || missing+=("ACR_NAME")
[ -n "$ACA_ENV" ]        || missing+=("ACA_ENV")
if [ ${#missing[@]} -gt 0 ]; then
  echo "ERROR: missing required deployment settings:" >&2
  for m in "${missing[@]}"; do echo "  - $m" >&2; done
  echo >&2
  echo "Set them in deploy/.env (copy deploy/.env.example) or export them." >&2
  exit 1
fi

# A login server here yields "myreg.azurecr.io.azurecr.io" and an unhelpful
# DNS failure at push. Catch it while the message can still explain itself.
case "$ACR_NAME" in
  *.azurecr.io*)
    echo "ERROR: ACR_NAME must be the registry NAME, not its login server." >&2
    echo "       Use '${ACR_NAME%%.azurecr.io*}', not '${ACR_NAME}'." >&2
    exit 1 ;;
esac

# Timestamp, not a bare git sha: `containerapp update --image` with the tag
# the app is already on is a no-op, so rebuilding without committing would
# redeploy the previous image and report success. The sha is appended for
# traceability, never used alone.
TAG="$(date -u +%Y%m%d-%H%M%S)"
_sha="$(git rev-parse --short HEAD 2>/dev/null || true)"
# `if`, not `[ ... ] && ...`: the latter is exempt from set -e only by a
# subtlety of && lists, and is the kind of line someone later "tidies" into
# an abort on every non-git checkout.
if [ -n "$_sha" ]; then TAG="${TAG}-${_sha}"; fi
REGISTRY="${ACR_NAME}.azurecr.io"

echo "==> Target"
echo "    group=${RESOURCE_GROUP}  registry=${REGISTRY}  env=${ACA_ENV}  tag=${TAG}"
if [ -n "$PG_CONN" ]; then
  echo "    database: Postgres (durable)"
else
  echo "    database: SQLite in-container -- reviews are lost on restart"
fi

echo "==> Building images in ACR"
# Root context: the API image carries the dataset (see backend/Dockerfile).
az acr build --registry "$ACR_NAME" --image "sdoc-api:${TAG}" -f backend/Dockerfile .
az acr build --registry "$ACR_NAME" --image "sdoc-web:${TAG}" ./frontend

# The registry is pulled with the app's own managed identity, so no registry
# password is stored anywhere. AcrPull is granted after the app exists.
api_env=(DATA_SOURCE=/data)
secrets=()
if [ -n "$PG_CONN" ]; then
  secrets+=(--secrets "db-url=${PG_CONN}")
  api_env+=(DATABASE_URL=secretref:db-url)
fi
if [ -n "$FIREBASE_PROJECT_ID" ]; then
  api_env+=("FIREBASE_PROJECT_ID=${FIREBASE_PROJECT_ID}")
fi

echo "==> Deploying API (internal ingress)"
if az containerapp show --name sdoc-api --resource-group "$RESOURCE_GROUP" &>/dev/null; then
  # The secret must exist before anything references it. --secrets only
  # exists on the create path, so an app first deployed without PG_CONN has
  # no db-url secret and DATABASE_URL=secretref:db-url is rejected.
  # `secret set` is create-or-update, so re-running is harmless.
  if [ -n "$PG_CONN" ]; then
    az containerapp secret set --name sdoc-api --resource-group "$RESOURCE_GROUP" \
      --secrets "db-url=${PG_CONN}" --output none
  fi
  az containerapp update --name sdoc-api --resource-group "$RESOURCE_GROUP" \
    --image "${REGISTRY}/sdoc-api:${TAG}" --output none
  az containerapp update --name sdoc-api --resource-group "$RESOURCE_GROUP" \
    --set-env-vars "${api_env[@]}" --output none
else
  az containerapp create \
    --name sdoc-api --resource-group "$RESOURCE_GROUP" --environment "$ACA_ENV" \
    --image "${REGISTRY}/sdoc-api:${TAG}" \
    --system-assigned \
    --target-port 8000 --ingress internal \
    --min-replicas 1 --max-replicas 3 --cpu 1 --memory 2Gi \
    "${secrets[@]}" --env-vars "${api_env[@]}" --output none

  # Let the app pull from ACR under its own identity.
  principal="$(az containerapp show --name sdoc-api --resource-group "$RESOURCE_GROUP" \
                 --query identity.principalId -o tsv)"
  acr_id="$(az acr show --name "$ACR_NAME" --query id -o tsv)"
  az role assignment create --assignee "$principal" --role AcrPull \
    --scope "$acr_id" --output none 2>/dev/null || true
  az containerapp registry set --name sdoc-api --resource-group "$RESOURCE_GROUP" \
    --server "$REGISTRY" --identity system --output none
fi

API_FQDN="$(az containerapp show --name sdoc-api --resource-group "$RESOURCE_GROUP" \
             --query properties.configuration.ingress.fqdn -o tsv)"
# Deploying web against an empty FQDN is how sdoc-web ended up with no
# BACKEND_URL and crash-looped. Refuse rather than ship it.
if [ -z "$API_FQDN" ]; then
  echo "ERROR: sdoc-api has no ingress FQDN; web would get an empty BACKEND_URL." >&2
  exit 1
fi
echo "    api fqdn=${API_FQDN}"

echo "==> Deploying web (external ingress, proxying /api to the API)"
if az containerapp show --name sdoc-web --resource-group "$RESOURCE_GROUP" &>/dev/null; then
  az containerapp update --name sdoc-web --resource-group "$RESOURCE_GROUP" \
    --image "${REGISTRY}/sdoc-web:${TAG}" --output none
  # `update --image` does not carry env vars, so an app created with a bad
  # BACKEND_URL could never be repaired by re-running the deploy.
  az containerapp update --name sdoc-web --resource-group "$RESOURCE_GROUP" \
    --set-env-vars "BACKEND_URL=https://${API_FQDN}" --output none
else
  az containerapp create \
    --name sdoc-web --resource-group "$RESOURCE_GROUP" --environment "$ACA_ENV" \
    --image "${REGISTRY}/sdoc-web:${TAG}" \
    --system-assigned \
    --target-port 80 --ingress external \
    --min-replicas 1 --max-replicas 2 --cpu 0.5 --memory 1Gi \
    --env-vars "BACKEND_URL=https://${API_FQDN}" \
    --output none

  principal="$(az containerapp show --name sdoc-web --resource-group "$RESOURCE_GROUP" \
                 --query identity.principalId -o tsv)"
  acr_id="$(az acr show --name "$ACR_NAME" --query id -o tsv)"
  az role assignment create --assignee "$principal" --role AcrPull \
    --scope "$acr_id" --output none 2>/dev/null || true
  az containerapp registry set --name sdoc-web --resource-group "$RESOURCE_GROUP" \
    --server "$REGISTRY" --identity system --output none
fi

WEB_FQDN="$(az containerapp show --name sdoc-web --resource-group "$RESOURCE_GROUP" \
             --query properties.configuration.ingress.fqdn -o tsv)"
echo "==> Live at https://${WEB_FQDN}"
echo "    Trigger the first pipeline run from the UI, or:"
echo "    curl -X POST https://${WEB_FQDN}/api/runs"
