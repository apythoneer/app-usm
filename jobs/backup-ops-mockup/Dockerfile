# Standalone static showcase for the SIP "Backup Operations" mockup.
# Mirrors the SIP frontend image: rootless nginx (uid 101), binds 8080, so it
# drops into the dev EKS cluster with no extra securityContext needed.
FROM nginxinc/nginx-unprivileged:alpine
COPY --chown=nginx:nginx nginx.conf /etc/nginx/conf.d/default.conf
COPY --chown=nginx:nginx index.html /usr/share/nginx/html/index.html
EXPOSE 8080
