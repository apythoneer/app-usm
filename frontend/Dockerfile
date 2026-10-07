FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

# Rootless nginx: runs as uid 101 and binds 8080 (non-privileged), so the same
# image satisfies the AKS prod Azure Policy (runAsNonRoot + readonly rootfs)
# while remaining drop-in for on-prem compose and dev EKS (both already use 8080).
FROM nginxinc/nginx-unprivileged:alpine
USER root
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
USER 101
EXPOSE 8080
