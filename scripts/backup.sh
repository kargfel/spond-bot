#!/bin/sh
# SpondBot database backups. Runs in the compose `backup` service (postgres:16-alpine).
#
#   backup.sh loop    back up now, then every BACKUP_INTERVAL_HOURS (the service default)
#   backup.sh once    one backup, then prune; exits non-zero on failure
#   backup.sh check   exit 0 if a backup newer than 2 x BACKUP_INTERVAL_HOURS exists
#
# Connection settings come from the standard libpq variables (PGHOST, PGUSER,
# PGDATABASE, PGPASSWORD). Dumps use pg_dump's custom format; restore them with
# pg_restore (see docs/setup.md). Old dumps are only pruned after a successful
# backup, so a run of failures never deletes the last good copy.
set -u
# Dumps hold every member's encrypted Spond credentials and the password hashes:
# readable by the owner only, whatever the host's default umask is.
umask 077

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
BACKUP_INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-24}"

log() { echo "[backup] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2; }

backup_once() {
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    final="$BACKUP_DIR/spond_bot-$stamp.dump"
    partial="$BACKUP_DIR/.spond_bot-$stamp.dump.partial"

    if ! pg_dump --format=custom --no-owner -f "$partial"; then
        rm -f "$partial"
        log "backup failed: pg_dump exited with an error; nothing was written"
        return 1
    fi
    mv "$partial" "$final"
    log "wrote $final ($(wc -c < "$final") bytes)"

    # Retention: keep BACKUP_KEEP_DAYS days of dumps; clear abandoned partial files.
    find "$BACKUP_DIR" -maxdepth 1 -type f -name 'spond_bot-*.dump' -mtime +"$BACKUP_KEEP_DAYS" \
        -exec rm -f {} \; -exec echo "[backup] pruned {}" \; >&2
    find "$BACKUP_DIR" -maxdepth 1 -type f -name '.spond_bot-*.dump.partial' -mtime +1 -exec rm -f {} \;
    return 0
}

check_recent() {
    max_age_min=$((BACKUP_INTERVAL_HOURS * 60 * 2))
    recent=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'spond_bot-*.dump' -mmin -"$max_age_min" | head -n 1)
    [ -n "$recent" ]
}

case "${1:-loop}" in
    once)
        backup_once
        ;;
    check)
        check_recent
        ;;
    loop)
        log "starting: every ${BACKUP_INTERVAL_HOURS}h into $BACKUP_DIR, keeping ${BACKUP_KEEP_DAYS} days"
        while :; do
            backup_once || log "will retry at the next interval"
            sleep "$((BACKUP_INTERVAL_HOURS * 3600))"
        done
        ;;
    *)
        echo "usage: backup.sh [loop|once|check]" >&2
        exit 2
        ;;
esac
