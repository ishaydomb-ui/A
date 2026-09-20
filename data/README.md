# Clinical formulary data

The authoritative medication workbook, as extracted from the clinician's
source table. This is the content the catalogue publishes.

It ships in the repository rather than being uploaded to each server
because it is the deployable content of this application, and because the
people who operate it do so from a phone over SSH, where copying a file
onto the host is not practical.

Loaded with:

```bash
docker compose -f docker-compose.local.yml -f docker-compose.permanent.yml \
  exec api node dist/scripts/load-formulary.js /formulary/formulary-2026-09.xlsx
```

The directory is mounted read-only into the API container at `/formulary`.
See `apps/api/src/scripts/load-formulary.ts` for what the load does and
what it records.

To publish a revised workbook, add it here under a new dated name, commit,
deploy and run the loader against it. Each load supersedes the records with
a new version and retires the previous one; nothing is deleted.
