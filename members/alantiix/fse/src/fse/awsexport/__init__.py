"""``fse export-aws``: the Data_Cache export to S3, Glue, Athena and SageMaker.

The pipeline has five stages, each idempotent, so a rerun only redoes work
whose inputs changed:

1. plan (:mod:`fse.awsexport.source`): read-only discovery of the cache;
2. stage (:mod:`fse.awsexport.staging`, :mod:`fse.awsexport.transform`):
   compacted, Hive-partitioned Parquet in a local staging directory;
3. bucket (:mod:`fse.awsexport.bucket`): create or verify a private bucket;
4. sync (:mod:`fse.awsexport.sync`): upload changed files, delete superseded parts;
5. glue (:mod:`fse.awsexport.glue`): one Glue table per output table, with
   partition projection.

:mod:`fse.awsexport.runner` runs them. ``--stage-only`` runs stages 1-2 and
never imports boto3; ``--dry-run`` writes nothing and makes only the three
read-only AWS calls that :mod:`fse.awsexport.aws` allows.

Layout under ``s3://{bucket}/{prefix}/`` (staging mirrors it under ``data/``)::

    heatmaps/symbol=SPX/metric=gamma/month=2025-10/part-00000.parquet
    bars/instrument=MES/part-00000.parquet
    vix_bars/instrument=VIX/part-00000.parquet
    vix_daily/part-00000.parquet
    darkpool/ticker=SPY/part-00000.parquet
    darkpool_fetched_dates/part-00000.parquet
    regime_medians/part-00000.parquet

Invariants (design §10):

- The cache is never written: the catalog is opened ``mode=ro``, files are only
  read, and the staging directory must lie outside the cache.
- Only ``complete`` windows and bar sessions are exported.
- Exported rows equal the source rows bit for bit.
- Every S3 key, staging path and Glue location comes from the helpers in
  :mod:`fse.awsexport.tables`.
- Nothing is deleted in S3 except superseded ``part-NNNNN.parquet`` keys of a
  staged partition, and nothing in staging outside ``.work/`` and
  ``data/{rel_path}``.
- The bucket is never public.
- Dry-run makes only the three read-only resource operations.
"""
