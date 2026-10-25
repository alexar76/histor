#!/usr/bin/env bash
# One Telegram message to the operator through the ARGUS bot (@Argis3Bot), installed as
# /usr/local/sbin/histor-notify. It reads AICOM_ALERT_TELEGRAM_TOKEN and AICOM_ALERT_TELEGRAM_CHAT from
# /etc/histor-notify.env (0600, root): the pair the ecosystem alerter on skopos-vps uses. The token
# never reaches a command line or a log: curl reads the URL from a config on its standard input.
#
#   histor-notify "text"
set -euo pipefail
CONF="${HISTOR_NOTIFY_ENV:-/etc/histor-notify.env}"
text="${1:?usage: histor-notify <text>}"
if [[ ! -r "$CONF" ]]; then
  echo "no $CONF: nothing sent" >&2
  exit 1
fi
value() { grep -E "^$1=" "$CONF" | head -1 | cut -d= -f2- | tr -d "\"' "; }
token="$(value AICOM_ALERT_TELEGRAM_TOKEN)"
chat="$(value AICOM_ALERT_TELEGRAM_CHAT)"
[[ -n "$token" && -n "$chat" ]] || { echo "token or chat missing in $CONF: nothing sent" >&2; exit 1; }
printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" \
  | curl -sS --fail --max-time 20 -K - --data-urlencode "chat_id=$chat" --data-urlencode "text=$text" \
      --data "disable_web_page_preview=true" -o /dev/null
echo "sent"
