# Static build served by an unprivileged nginx (non-root, port 8080). NOT built in the session that wrote it (no working Docker
# daemon there); CI's docker job is the first build. API base URLs are baked in at build time (Vite):
#   docker build --build-arg VITE_P3_URL=https://<gateway> --build-arg VITE_P1_URL=https://<gateway>/v1/data .
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG VITE_P1_URL=/p1
ARG VITE_P3_URL=/p3
ENV VITE_P1_URL=$VITE_P1_URL VITE_P3_URL=$VITE_P3_URL
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
