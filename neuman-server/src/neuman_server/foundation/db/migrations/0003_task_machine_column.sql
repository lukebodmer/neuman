-- 0003_task_machine_column.sql — the column exists before anyone is asked to fill it.
--
-- `tasks.machine` is required (D112): a recipe is about files on the computer
-- its author was looking at, and no column on `tasks` says which. `0004`
-- refuses to guess and stops once, printing the statement an operator runs to
-- supply the label.
--
-- The operator can only run that statement if the column exists, and this file
-- is why it does. The first shape of this change put `ADD COLUMN` and the
-- refusal in the same file, in one transaction (D103): the refusal rolled the
-- `ADD COLUMN` back, so the statement the migration printed named a column that
-- was not there and could never be run (N188). A column an operator must
-- backfill is added here, nullable, in a transaction that commits; the next
-- file is the enforcement.

ALTER TABLE tasks
    ADD COLUMN machine TEXT
        CONSTRAINT tasks_machine_shaped
        CHECK (machine ~ '^[a-z0-9][a-z0-9._-]{0,63}$');
