BEGIN TRANSACTION;
CREATE TABLE application_versions (
    version_id TEXT NOT NULL PRIMARY KEY,
    version_name TEXT NOT NULL UNIQUE,
    version_timestamp INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
, "application_name" TEXT DEFAULT NULL);
INSERT INTO "application_versions" VALUES('b421ff6c-fda8-488c-8a6b-2d7eea32c6db','contract-sdk-upgrade',1790804515000,1790804515000,'printstash');
CREATE TABLE dbos_migrations (version INTEGER NOT NULL PRIMARY KEY);
INSERT INTO "dbos_migrations" VALUES(108);
CREATE TABLE notifications (
    message_uuid TEXT NOT NULL DEFAULT (hex(randomblob(16))) PRIMARY KEY,
    destination_uuid TEXT NOT NULL,
    topic TEXT,
    message TEXT NOT NULL,
    created_at_epoch_ms INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000), "serialization" TEXT DEFAULT NULL, "consumed" BOOLEAN NOT NULL DEFAULT FALSE,
    FOREIGN KEY (destination_uuid) REFERENCES workflow_status(workflow_uuid) 
        ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE TABLE operation_outputs (
    workflow_uuid TEXT NOT NULL,
    function_id INTEGER NOT NULL,
    function_name TEXT NOT NULL DEFAULT '',
    output TEXT,
    error TEXT,
    child_workflow_id TEXT, started_at_epoch_ms BIGINT, completed_at_epoch_ms BIGINT, "serialization" TEXT DEFAULT NULL, "application_name" TEXT DEFAULT NULL,
    PRIMARY KEY (workflow_uuid, function_id),
    FOREIGN KEY (workflow_uuid) REFERENCES workflow_status(workflow_uuid) 
        ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE TABLE queues (
    queue_id TEXT PRIMARY KEY DEFAULT (hex(randomblob(16))),
    name TEXT NOT NULL UNIQUE,
    concurrency INTEGER,
    worker_concurrency INTEGER,
    rate_limit_max INTEGER,
    rate_limit_period_sec REAL,
    priority_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    partition_queue BOOLEAN NOT NULL DEFAULT FALSE,
    polling_interval_sec REAL NOT NULL DEFAULT 1.0,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
, "application_name" TEXT DEFAULT NULL, "partition_concurrency" INTEGER DEFAULT NULL, "partition_worker_concurrency" INTEGER DEFAULT NULL, "partition_rate_limit_max" INTEGER DEFAULT NULL, "partition_rate_limit_period_sec" REAL DEFAULT NULL);
INSERT INTO "queues" VALUES('D151CAAA394E9804B2660A6EDE6E6544','derive.light',NULL,4,NULL,NULL,1,0,1.0,1790804515000,1790804515458,'printstash',NULL,NULL,NULL,NULL);
CREATE TABLE streams (
    workflow_uuid TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    "offset" INTEGER NOT NULL, function_id INTEGER NOT NULL DEFAULT 0, "serialization" TEXT DEFAULT NULL,
    PRIMARY KEY (workflow_uuid, key, "offset"),
    FOREIGN KEY (workflow_uuid) REFERENCES workflow_status(workflow_uuid) 
        ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE TABLE workflow_events (
    workflow_uuid TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL, "serialization" TEXT DEFAULT NULL,
    PRIMARY KEY (workflow_uuid, key),
    FOREIGN KEY (workflow_uuid) REFERENCES workflow_status(workflow_uuid) 
        ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE TABLE workflow_events_history (
    workflow_uuid TEXT NOT NULL,
    function_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL, "serialization" TEXT DEFAULT NULL,
    PRIMARY KEY (workflow_uuid, function_id, key),
    FOREIGN KEY (workflow_uuid) REFERENCES workflow_status(workflow_uuid)
        ON UPDATE CASCADE ON DELETE CASCADE
);
CREATE TABLE workflow_schedules (
    schedule_id TEXT PRIMARY KEY,
    schedule_name TEXT NOT NULL UNIQUE,
    workflow_name TEXT NOT NULL,
    workflow_class_name TEXT,
    schedule TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    context TEXT NOT NULL
, "last_fired_at" TEXT DEFAULT NULL, "automatic_backfill" BOOLEAN NOT NULL DEFAULT FALSE, "cron_timezone" TEXT DEFAULT NULL, "queue_name" TEXT DEFAULT NULL, "application_name" TEXT DEFAULT NULL);
CREATE TABLE workflow_status (
    workflow_uuid TEXT PRIMARY KEY,
    status TEXT,
    name TEXT,
    authenticated_user TEXT,
    assumed_role TEXT,
    authenticated_roles TEXT,
    request TEXT,
    output TEXT,
    error TEXT,
    executor_id TEXT,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000),
    application_version TEXT,
    application_id TEXT,
    class_name TEXT DEFAULT NULL,
    config_name TEXT DEFAULT NULL,
    recovery_attempts INTEGER DEFAULT 0,
    queue_name TEXT,
    workflow_timeout_ms INTEGER,
    workflow_deadline_epoch_ms INTEGER,
    inputs TEXT,
    started_at_epoch_ms INTEGER,
    deduplication_id TEXT,
    priority INTEGER NOT NULL DEFAULT 0
, queue_partition_key TEXT, forked_from TEXT, "owner_xid" TEXT DEFAULT NULL, "parent_workflow_id" TEXT DEFAULT NULL, "serialization" TEXT DEFAULT NULL, "delay_until_epoch_ms" BIGINT DEFAULT NULL, "was_forked_from" BOOLEAN NOT NULL DEFAULT FALSE, "rate_limited" BOOLEAN NOT NULL DEFAULT FALSE, "completed_at" BIGINT, "attributes" TEXT, "schedule_name" TEXT, "debounce_deadline_epoch_ms" BIGINT DEFAULT NULL, "is_debounced" BOOLEAN NOT NULL DEFAULT FALSE, "application_name" TEXT DEFAULT NULL);
INSERT INTO "workflow_status" VALUES('dbos-231-result:1','SUCCESS','printstash.job',NULL,NULL,NULL,NULL,'gASVLAAAAAAAAAB9lCiMBmpvYl9pZJSMD2Rib3MtMjMxLXJlc3VsdJSMB2F0dGVtcHSUSwF1Lg==',NULL,'legacy-executor',1790804515000,1790804515000,'contract-sdk-upgrade','',NULL,NULL,1,NULL,NULL,NULL,'gASVLQAAAAAAAAB9lCiMBGFyZ3OUjA9kYm9zLTIzMS1yZXN1bHSUSwGGlIwGa3dhcmdzlH2UdS4=',NULL,NULL,0,NULL,NULL,'78a8f174-76d1-4472-ae79-026b8fd9491c',NULL,'py_pickle',NULL,0,0,1790804515000,'null',NULL,NULL,0,'printstash');
INSERT INTO "workflow_status" VALUES('dbos-231-job:1','ENQUEUED','printstash.job',NULL,NULL,NULL,NULL,NULL,NULL,'legacy-executor',1790804515000,1790804515000,'contract-sdk-upgrade','',NULL,NULL,0,'derive.light',NULL,NULL,'gASVKgAAAAAAAAB9lCiMBGFyZ3OUjAxkYm9zLTIzMS1qb2KUSwGGlIwGa3dhcmdzlH2UdS4=',NULL,NULL,0,NULL,NULL,'56a39b03-04c2-4bd1-8e40-3001763848ca',NULL,'py_pickle',NULL,0,0,NULL,'null',NULL,NULL,0,'printstash');
CREATE INDEX workflow_status_created_at_index ON workflow_status (created_at);
CREATE INDEX idx_workflow_topic ON notifications (destination_uuid, topic);
CREATE INDEX "idx_notifications" ON "notifications" ("destination_uuid", "topic");
CREATE INDEX "idx_workflow_status_delayed" ON "workflow_status" ("delay_until_epoch_ms") WHERE status = 'DELAYED';
CREATE INDEX "idx_operation_outputs_completed_at_function_name" ON "operation_outputs" ("completed_at_epoch_ms", "function_name");
CREATE INDEX "idx_workflow_status_forked_from" ON "workflow_status" ("forked_from") WHERE "forked_from" IS NOT NULL;
CREATE INDEX "idx_workflow_status_parent_workflow_id" ON "workflow_status" ("parent_workflow_id") WHERE "parent_workflow_id" IS NOT NULL;
CREATE UNIQUE INDEX "uq_workflow_status_dedup_id" ON "workflow_status" ("queue_name", "deduplication_id") WHERE "deduplication_id" IS NOT NULL;
CREATE INDEX "idx_workflow_status_pending" ON "workflow_status" ("created_at") WHERE "status" = 'PENDING';
CREATE INDEX "idx_workflow_status_failed" ON "workflow_status" ("status", "created_at") WHERE "status" IN ('ERROR', 'CANCELLED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED');
CREATE INDEX "idx_workflow_status_in_flight" ON "workflow_status" ("queue_name", "status", "priority", "created_at") WHERE "status" IN ('ENQUEUED', 'PENDING');
CREATE INDEX "idx_workflow_status_rate_limited" ON "workflow_status" ("queue_name", "started_at_epoch_ms") WHERE "rate_limited" = TRUE;
CREATE INDEX "idx_workflow_status_completed_at" ON "workflow_status" ("completed_at") WHERE "completed_at" IS NOT NULL;
CREATE INDEX "idx_workflow_status_started_at" ON "workflow_status" ("started_at_epoch_ms") WHERE "started_at_epoch_ms" IS NOT NULL;
CREATE INDEX "idx_workflow_status_schedule_name" ON "workflow_status" ("schedule_name") WHERE "schedule_name" IS NOT NULL;
CREATE INDEX "idx_workflow_status_partition_dequeue_v2" ON "workflow_status" ("queue_name", "status", "queue_partition_key", "priority", "created_at", "workflow_uuid") WHERE "status" IN ('ENQUEUED', 'PENDING') AND "queue_partition_key" IS NOT NULL;
CREATE UNIQUE INDEX "uq_application_versions_owner_version"
    ON application_versions ("application_name", "version_name")
    WHERE "application_name" IS NOT NULL;
CREATE UNIQUE INDEX "uq_application_versions_unclaimed_version"
    ON application_versions ("version_name")
    WHERE "application_name" IS NULL;
COMMIT;
