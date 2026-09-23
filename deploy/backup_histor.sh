#!/usr/bin/env bash
# Nightly backup of HISTOR's log and keys. Installed as /usr/local/sbin/histor-backup and run by
# histor-backup.timer, both set up by scripts/deploy_histor.sh.
#
# What is irreplaceable: the issuer key (lose it and the log's identity is gone — a new key is a
# new log) and the Postgres log itself. Both live in single Docker volumes on one host, so this
# keeps local, dated copies: a pg_dump (custom format, checked with pg_restore --list) and a tar of
# the data volume (the keys and the tree-head marker). 0600 files in a 0700 directory, 14 days.
#
# Local copies survive a dropped volume or a botched restore, not the loss of the host. Copy
# /var/backups/histor somewhere else too (see docs/operations.md, "Backups").
set -euo pipefail

DIR="${HISTOR_BACKUP_DIR:-/var/backups/histor}"
KEEP_DAYS="${HISTOR_BACKUP_KEEP_DAYS:-14}"
PG="${HISTOR_PG_CONTAINER:-histor-histor-postgres-1}"
APP="${HISTOR_APP_CONTAINER:-histor-histor-1}"

umask 077
mkdir -p "$DIR"
chmod 700 "$DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"

dump="$DIR/histor-$stamp.dump"
docker exec "$PG" pg_dump -U histor -d histor -Fc > "$dump.part"
docker exec -i "$PG" pg_restore --list < "$dump.part" > /dev/null  # a dump that does not list is not a backup
mv "$dump.part" "$dump"

keys="$DIR/keys-$stamp.tar"
docker cp "$APP:/data" - > "$keys.part"  # a tar stream of the data volume: issuer.key, provider keys, sth-marker.json
tar -tf "$keys.part" | grep -q 'issuer.key$' || { echo "no issuer.key in the data volume copy" >&2; rm -f "$keys.part"; exit 1; }
mv "$keys.part" "$keys"

find "$DIR" -maxdepth 1 -type f \( -name 'histor-*.dump' -o -name 'keys-*.tar' \) -mtime +"$KEEP_DAYS" -delete
echo "histor backup: $(du -h "$dump" | cut -f1) log, keys copied → $DIR"
