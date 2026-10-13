FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

# Rootless nginx: runs as uid 101 and binds 8080, so the same image satisfies the
# AKS prod Azure Policy (runAsNonRoot + readonly rootfs) and is drop-in for on-prem
# compose and dev EKS (both already use 8080). COPY --chown so the 101 runtime user
# can READ the config + static files — a root-owned copy makes nginx fail to open
# /etc/nginx/conf.d/default.conf with EACCES and crash-loop.
FROM nginxinc/nginx-unprivileged:alpine
COPY --chown=nginx:nginx --from=builder /app/dist /usr/share/nginx/html
COPY --chown=nginx:nginx nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
