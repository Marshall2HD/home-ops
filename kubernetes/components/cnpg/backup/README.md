# cnpg/backup

Backs up the CloudNativePG cluster `${APP}-postgres` to Garage on Hypnos
(`s3://cnpg/<serverName>/`) with the Barman Cloud plugin: WAL is archived
continuously and a base backup runs nightly, kept for 30 days. Any point in
that window can be restored.

Use it from an app's `database` Kustomization (`components:` plus
`postBuild.substitute.APP`), and opt the Cluster in:

```yaml
spec:
  plugins:
    - name: barman-cloud.cloudnative-pg.io
      isWALArchiver: true
      parameters:
        barmanObjectName: ${APP}-postgres
```

## Restore

1. `flux suspend ks <app>` and scale the app to 0 so nothing writes.
2. In `database/cluster.yaml`, replace `bootstrap.initdb` with a recovery,
   and archive the restored cluster to a new path so it does not collide
   with the old WAL:

   ```yaml
   bootstrap:
     recovery:
       source: backup
       # recoveryTarget:
       #   targetTime: "2026-11-04 12:00:00+00"
   externalClusters:
     - name: backup
       plugin:
         name: barman-cloud.cloudnative-pg.io
         parameters:
           barmanObjectName: <app>-postgres
           serverName: <app>-postgres   # the path being restored from
   plugins:
     - name: barman-cloud.cloudnative-pg.io
       isWALArchiver: true
       parameters:
         barmanObjectName: <app>-postgres
         serverName: <app>-postgres-r1 # new path for the restored cluster
   ```

3. Delete the Cluster (`kubectl delete cluster <app>-postgres`), push, and
   let Flux recreate it from the backup.
4. `flux resume ks <app>`.

Keep the new `serverName`; the bootstrap block is only read when the
Cluster is created.
