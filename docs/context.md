# Context

An AI model can only keep so much of a conversation in view at once. That limit
is its **context window**, and this page explains what Neuman does when a
conversation outgrows it, what that costs you, and how to work with it.

## A conversation is not limited by the window

When a conversation gets too long for the model, Neuman replaces the oldest part
with a summary and carries on. So "read these forty files and tell me how they
fit together" finishes, rather than ending in an error halfway through.

The window differs a lot between models. A hosted model may hold a million
tokens, while a model on a single local graphics card may hold 65,536. Neuman
sizes everything from the model actually in use, so the same conversation is
summarized sooner on a small local model than on a large hosted one.

## The context bar

The bar under the message box shows how full the conversation is. It fills
toward the point where a summary will happen, **not** toward the model's whole
window, and the model's window is shown in its tooltip.

The two numbers are different because the window also has to hold the reply and
a safety margin. On a 65,536-token local model, a summary happens at about
30,000 tokens of conversation. A full bar means a summary is near. It does not
mean anything is about to fail.

## What a summary keeps, and what it loses

A summary keeps:

- **your original request, word for word**, so the agent never loses sight of
  what it was asked to do;
- **a structured summary** of the earlier conversation: the task, the facts
  established (with exact values), the decisions made and why, the files
  produced, what was ruled out, and the next step;
- **the most recent part of the conversation, word for word**.

It can lose detail. A summary is shorter than what it replaces, so a precise
figure or a passing remark from early in a long conversation may not survive.
When it happens, the conversation says so in a line like:

```
…context filled; summarized the earlier 46 messages and continuing
```

If the agent later seems to have forgotten something from early on, that line
is where it went.

Large tool output is handled more cheaply before any summary is needed. When a
command or a file read produces a lot of text, the full output is saved to a
file on your machine and the agent is shown a preview and the file's location.
Once that output is old, only the location is kept, and the agent can read the
file again whenever it needs to. Nothing is lost that way.

## Nothing is lost from the record

A summary shortens what the *model* sees, not what Neuman keeps. Every message,
every tool call and every result stays in the session's record in full. Scroll
back and the whole conversation is there as it happened, and an administrator
can read any session's complete history with `neuman-admin session events`.

## Working with it

- **For work that depends on precise early detail, use several shorter
  conversations** rather than one very long one, and restate what matters at
  the start of each. A long conversation will not fail, but it can forget.
- **Fork a conversation** to try a different direction from an earlier point,
  without carrying everything that came after it.
- **On a local model, the window is a setting.** An administrator can raise it
  on the model's page, at the cost of GPU memory, and can store it in compact
  form to fit roughly twice as much on the same card (`docs/setup.md`).

How the summary is built, when exactly it happens, and why it is shaped the way
it is are in [the harness](harness.md).
