-- 0004_machine_required.sql — a conversation and a task always name a computer.
--
-- `session_branches.machine` was added nullable by 0002, and `tasks.machine` by
-- 0003, both because a value written before the column cannot be derived. The
-- core guessed anyway for a while — "the account's most recently connected
-- client" — and that guess is what produced the bug this file exists to end. A
-- branch written before machines existed carried a `working_dir` from a
-- computer nobody had identified; adopting whichever client happened to be
-- connected pointed the conversation at the other computer, and every dispatch
-- then ran in a directory that was not there — or, worse, one that *was* there
-- and held different files.
--
-- So the column becomes required, and the migration does not choose for
-- anybody. A machine is the one thing about a branch or a task that cannot be
-- derived from it: it is where the person was standing. Guessing it is how the
-- failure happened, and a migration that guessed would bake the same mistake
-- into the schema.
--
-- The cost is an upgrade step, stated in the error rather than in a changelog:
-- an appliance with rows from before the column stops once, prints what to run,
-- and starts when it has been run. A fresh appliance has no such rows and sees
-- nothing.

DO $$
DECLARE
    branches bigint;
BEGIN
    SELECT count(*) INTO branches FROM session_branches WHERE machine IS NULL;
    IF branches > 0 THEN
        RAISE EXCEPTION USING
            MESSAGE = format(
                '%s conversation branch(es) do not name a computer', branches
            ),
            HINT = $hint$
A conversation runs on one computer, and the appliance will not guess which.
Set the machine that is true for each row, then start Neuman again. The
<label> is a machine the owning account has connected from; the labels are in
the `machines` table.

    UPDATE session_branches
       SET machine = '<label>', working_dir = NULL, attachments = '[]'::jsonb
     WHERE machine IS NULL;

The working directory and attachments are cleared with the machine because they
are paths on a computer, and which one is exactly what was never recorded.
$hint$;
    END IF;
END $$;

ALTER TABLE session_branches ALTER COLUMN machine SET NOT NULL;

-- `tasks.machine` was added by 0003. Same refusal, same reason: a recipe is
-- about files somewhere, and the person who wrote it was looking at them.
DO $$
DECLARE stragglers bigint;
BEGIN
    SELECT count(*) INTO stragglers FROM tasks WHERE machine IS NULL;
    IF stragglers > 0 THEN
        RAISE EXCEPTION USING
            MESSAGE = format('%s task(s) do not name a computer', stragglers),
            HINT = $hint$UPDATE tasks SET machine = '<label>' WHERE machine IS NULL;$hint$;
    END IF;
END $$;

ALTER TABLE tasks ALTER COLUMN machine SET NOT NULL;
