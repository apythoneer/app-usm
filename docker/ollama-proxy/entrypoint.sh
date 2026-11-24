#!/bin/sh
# Fetch the proxy token from the local KeePass broker, render nginx.conf, start nginx.
# The token is never printed. Rotate: change the KeePass entry, then restart this container.
set -eu

: "${KEEPASS_BASE:=http://127.0.0.1:2000/keepass}"
: "${KEEPASS_ENTRY:=SIP_Ollama_Proxy_Token}"
: "${LISTEN_PORT:=11435}"
: "${UPSTREAM:=127.0.0.1:11434}"

i=0
until body=$(wget -qO- "$KEEPASS_BASE/$KEEPASS_ENTRY" 2>/dev/null); do
  i=$((i + 1))
  [ "$i" -ge 30 ] && { echo "KeePass broker unreachable at $KEEPASS_BASE" >&2; exit 1; }
  sleep 2
done

token=$(printf '%s' "$body" | sed -n 's/.*"Password"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
case "$token" in
  "") echo "KeePass entry '$KEEPASS_ENTRY' missing or has no password" >&2; exit 1 ;;
esac
# Keep it safe to embed in nginx config: letters, digits, - and _ only, 32+ chars.
if ! printf '%s' "$token" | grep -Eq '^[A-Za-z0-9_-]{32,}$'; then
  echo "Token in '$KEEPASS_ENTRY' must be 32+ chars of A-Z a-z 0-9 - _ only" >&2; exit 1
fi

sed -e "s|__TOKEN__|$token|" -e "s|__LISTEN_PORT__|$LISTEN_PORT|" -e "s|__UPSTREAM__|$UPSTREAM|" \
    /etc/ollama-proxy/nginx.conf.template > /tmp/nginx.conf
unset token body
echo "ollama-proxy: listening on :$LISTEN_PORT -> $UPSTREAM (token from KeePass '$KEEPASS_ENTRY')"
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
