# Build the static bundle, then serve it from nginx. The runtime image
# carries no Node and no build tooling.
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci || npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# templates/ (not conf.d/) so the entrypoint runs envsubst and BACKEND_URL
# is resolved at container start rather than baked in at build time.
COPY nginx.conf /etc/nginx/templates/default.conf.template
# Sourced before envsubst so a missing BACKEND_URL degrades to a warning
# and a sane default instead of an nginx crash-loop.
COPY 05-default-backend-url.envsh /docker-entrypoint.d/05-default-backend-url.envsh
# Which variables envsubst may replace. Anchored, so nginx's own $host,
# $uri and friends survive. Set here rather than per-deploy: leaving it to
# the caller is how the resolver variable gets missed.
ENV NGINX_ENVSUBST_FILTER="^(BACKEND_URL|NGINX_LOCAL_RESOLVERS)$"
# The image's 15-local-resolvers.envsh is opt-in: without this flag it
# returns early and NGINX_LOCAL_RESOLVERS is never populated, leaving the
# resolver directive unsubstituted.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
EXPOSE 80
# 127.0.0.1, not localhost: inside the container localhost resolves to ::1
# first and nginx here listens on IPv4 only.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
