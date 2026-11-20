# What Neuman is for

Neuman lets a business run an AI agent on its own servers: one that reads,
writes and runs things in the business's real files, never reaches further than
the person it acts for, and shares nothing with anyone the business did not
choose.

It answers a narrow question: *how do you give an AI agent real shell and
filesystem capability without giving it more reach than the person asking?*
Because of how that answer turned out, it is also an opinionated answer to a
wider one: how a business puts AI to work on its most sensitive data without
handing that data to someone else.

## Who it is for

Anyone who wants the strongest AI they can get working on their real data,
without handing that data to someone else. Neuman is for you if you want to:

- **Keep your clients' data safe.** Client files, patient records,
  policyholder details and personnel matters stay on your hardware.
- **Keep your own data safe.** Unreleased work, finances, strategy and
  unpublished research stay yours, including data a contract, grant condition
  or regulation forbids sending to a third party.
- **Own the computational power.** Hardware you own is never rationed, metered
  per seat or per token, or repriced as more people use it.
- **Avoid lock-in to vendor-specific solutions.** Neuman is open source and its
  models are open weights on your own disk, so nobody else can change the
  price, the model or the terms, or switch it off.
- **Customize your AI to your business.** Add your own tools, switch on the
  packages you need, and tell the agent what only your organisation knows.
- **Give every person an AI fitted to their role.** Each account gets its own
  agent, with exactly the tools, files and models that person's work calls for.
- **Keep a record of everything your AI has done.** Every tool call, approval
  and policy change is written to a tamper-evident record you can review
  afterwards.

Neuman is built for small and medium-sized businesses, and works just as well
for one person or a family.

## It runs on your hardware

Neuman is installed on a server you own, in your building or wherever you keep
your machines. The models, the accounts, the policy, the database and the
record of everything the agent did all live there. When the model runs on that
server's own GPU, nothing you ask and nothing the agent reads ever leaves your
network.

This matters more for AI than for most software. An agent is only useful once
you give it the things that matter: the contracts, the books, the customer
history, the drafts nobody should see. A cloud service can promise to keep
those private, but you cannot check the promise, and the vendor can change it.
When the system runs on your own hardware, privacy is not a promise from
anyone. It is simply where your data is.

**Running locally does not mean settling for a weak model.** Neuman runs
strong open-weight models for you, and supports a range of them: from one that
fits a single consumer graphics card to frontier-class models for a server with
hundreds of gigabytes of GPU memory. An administrator picks one in the administration page, Neuman downloads
and verifies it, and each model is tuned for what it actually is
(`docs/setup.md`).

If you also want a cloud model, for work that is already public, it can sit
beside the local ones. Your roles decide who may use it, so whether a
conversation leaves the building is your decision, made once, rather than
something each person has to remember.

## How it keeps your AI contained

Neuman is built on one rule: **your AI can only do what the person using it is
allowed to do, and nothing they have not approved.** That rule is not a request
in the AI's instructions. It is enforced by the computer itself, so it holds
even when the AI gets something wrong. The [security model](security-model.md)
explains exactly how.

A model asked to behave well behaves well until it doesn't: until it is
confused, or convinced, or simply wrong. So Neuman does not rely on the model.
It splits in two:

- **The appliance decides.** It holds the accounts, the permissions, the
  approvals, the models and the record, and it has no way into anyone's files.
- **The person's machine acts.** A small program there runs the agent's tools
  under that person's own login, so the agent can never reach beyond what that
  person already can. Who may open a folder is still decided by the file
  server's own permissions, exactly as it was before Neuman existed.

Within that limit, the agent usually gets much less. Each person's role names
the tools, folders and models their agent may use and when it must ask first,
and every action the agent proposes is checked against it before it runs. Every
decision goes into a record in which any later edit shows, so trust here means
verifiable, not promised.

## Open, and built to be extended

Neuman is open source, and it is meant to grow. It is modular: a tool is a
manifest declaring what it needs plus a program the person's machine runs, and
a set of related tools ships as a **package** an administrator switches on from
the administration page. Packages let the agent work in the applications your
people already use, alongside them, so new toolsets can be added as your work
calls for them and left out when it does not.

The point is that a business should be able to make Neuman fit exactly the work
it does, and that every addition goes through the same gate, the same
approvals and the same record as everything else, so extending it never means
widening what it can reach behind anyone's back.
