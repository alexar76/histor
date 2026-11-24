# HISTOR: link shorteners listed apart — live 2026-11-24

The owner said «делай, начни с HISTOR». This follows a DEV.to question from Lycore Development: could only new hosts
outside the server's own domain and a known registry list be flagged? On the 2026-11-22 sample of 2,000 changes,
that filter left 34 of the 58 changes with a new address, while a list of link shorteners matched exactly one
(a bit.ly).

**The change** (aicom `d3bcc7da3`):
- `diffing.LINK_SHORTENERS`;
- `newShortLinks` in a change summary;
- `changed_short_links` for records stored before;
- shown in the Atom feed, on the server page (label in five languages), in `histor.changes@v1` and in Watch
  webhooks.

## Production

All times are server UTC.

| | |
|---|---|
| Image | `histor-histor:shortlinks-20261124` = `sha256:2c44c3dfb004fe3b58adcb82dea58dae6a9c29f04065114ad4fa9f25a3c33ed3` |
| Built from | the live image `sha256:c7f1d41a6e7d86a37d9fc810bccc0f373777e57f591033df582911d551cf8a2b` (another session's rebuild of 06:11, tagged `histor-histor:pre-shortlinks-20261124`) plus `histor/diffing.py`, `histor/app.py`, `histor/security_events.py`, `docs/landing/assets/desk.js`, `i18n.js` and the four `i18n/*.js` of `d3bcc7da3`. The nine files in the live container equalled main before the overlay (sha256 checked); WARDEN 0.38.0 is unchanged |
| Switch | `switch.sh`: compose builds `histor-histor:latest` from `/opt/histor`, so the switch retags `latest` and recreates the service with `--no-build`. No crawl was open. It started 09:24:48, was healthy at 09:25:16, and seven routes answered 200 (`/`, `/health`, `/api/v1/stats`, `/api/v1/changes`, `/feed.xml`, `/assets/desk.js`, `/api/v1/log/checkpoint/cosigned`). The short-link check inside the container passed: `SWITCH-OK` 09:25:54 (`switch.log`) |
| `/opt/histor` | The same nine files were copied in, so the next `compose build` keeps them. Previous copies are in `/root/histor-shortlinks-20261124/opt-prev` |
| MOMUS | The image was copied to admin-vps with `docker save \| docker load` through the Mac, then `skopos-warden-quality rebind sha256:2c44c3df…`. `live-image.json` names it, and the build digest is unchanged (`cbb3a54f…`) |
| Rollback | On the HISTOR host: `/root/histor-shortlinks-20261124/switch.sh rollback`. Then on admin-vps: `skopos-warden-quality rebind sha256:c7f1d41a6e7d86a37d9fc810bccc0f373777e57f591033df582911d551cf8a2b` |

Stored records are not rewritten. Changes recorded before the switch carry no `newShortLinks`:
- the feed, `histor.changes@v1` and webhooks read their shorteners from the stored `newAddresses`;
- the server page shows the field only where it is stored.

## Found on the way and fixed

1. **MOMUS was bound to a stale image.** From the 06:11 rebuild on, production ran `c7f1d41a…`, but MOMUS was still
   bound to `5759d9fa…` (the image of 2026-11-23). The rebind above settles it.
2. **MOMUS could not read the runtime record.** The 06:11 deploy installed the repository's nginx site, and that
   site had never carried the hand-added `include` of the host-local snippet `histor-runtime-location.conf`. As a
   result, `/.well-known/aicom-histor-runtime.json` answered 404, and the first rebind failed with
   "RuntimeUnavailable". The tonight 03:30 MOMUS run would have failed the same way.
   - The repository site now includes it as an optional glob, like the backup-status snippet:
     `include /etc/nginx/snippets/histor-runtime-location.con[f];`.
   - That site is installed live. The previous live file is `/root/histor-shortlinks-20261124/nginx-live-before.conf`.
   - After the change, admin-vps reads the record (200), and the alerter still reads `/.aicom/backup-status.json`
     (200).
3. **Wrong line order in the live nginx site.** At 06:55 the keep-four rollout (`999d25fb3`) applied its nginx hunk
   with a context-free patch. That put the Hugging Face fallback above the trailing-punctuation redirect of
   2026-11-04, so `/datasets/<date>/file)` went to Hugging Face with the `)`. The repository's order is now live:
   `…manifest.json)` gives a 301 to the clean path, and then a 302 to Hugging Face.
4. **Wrong times in the keep-four record.** The Mac's clock runs about 56 minutes ahead of UTC. The keep-four commit
   (`999d25fb3`) says "~07:2xZ"; the files were installed at 06:55 server time. The two-dumps change (`6bae42f40`)
   was installed at 08:19.
5. **Deploy files out of date in `/opt/histor`.** `deploy_histor.sh` installs the deploy scripts and the nginx site
   from `/opt/histor`. That tree still held the files from before today, which a deploy would have put back. The
   following were copied in (previous copies in `/root/histor-shortlinks-20261124/opt-prev-deploy`):
   - `export_dataset.sh` (four snapshots);
   - `zenodo_reminder.sh`;
   - `backup_histor.sh` (two nights);
   - the nginx site;
   - `dataset_index.py`.
