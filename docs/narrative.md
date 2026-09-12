# Ora — the story

## 1. The thing that keeps happening

Four of us have a group chat. It has been going for years. And every week,
the same small failures:

- "Wait, did we ever decide a time for Saturday?"
- Someone asks a question, three people talk over it, and it just… scrolls
  away.
- A plan gets made in one app and changed in another, and now two people
  are working from different versions of it.
- Someone says "I'll check and get back to you," and nobody ever does,
  because nobody is keeping the list.

None of this is a productivity problem. Nobody is going to install a
project management tool for their friends. These are the small pieces of
context that friendships normally lose — and losing them is just what
group chats are like.

**Ora handles the small pieces of context that friendships normally lose.**

## 2. What it actually does

Most assistants belong to a person. You open an app, you type, it answers,
it forgets. **Ora belongs to a circle.** It sits in the group's real chats —
a Signal group and a WhatsApp group, the same four people, one shared
picture — and it works two ways.

**When you call it, it answers.** Someone tags it, it takes one fresh turn,
answers in the room, and that is the whole scope: one room, one exchange.
This is the part everyone already expects.

**When nobody calls it, it is still paying attention.** Messages keep
arriving. It keeps a running position on where each room's conversation
stands. It keeps a short list of what the group has left unresolved, each
one with the condition that would settle it. And every so often it asks
itself a different question from "what should I reply?" — it asks *is there
an occasion to say anything at all?*

Almost always, the answer is no.

## 3. Silence is the point

Here is the behaviour that is easy to miss and is the whole thesis.

The group can chat for twenty minutes — banter, a link, three "lol"s — and
Ora says nothing. That is not the agent being switched off. That is a
judgement it made, per window of conversation, and wrote down. You can see
the rows: `no_go`, `no_go`, `no_go`, each one with the reasoning turn
behind it, while the room chats on.

**Ora doesn't speak because a timer fired. It speaks because the
conversation reached a state where it has something useful to contribute.**

This is attention selection, not message routing. The question a relevance
gate answers is not "is this message addressed to me" — it is "has this
conversation reached a state worth contributing to." Most of the time it
has not, and the successful outcome is that nothing happens.

An assistant that answers every message is a chatbot in a group chat, and
a group chat with a chatbot in it is worse than one without.

## 4. And sometimes it starts the conversation

Two weeks' worth of the dinner thread goes quiet without anyone picking a
place. Nobody has tagged anything. Nobody has typed. Ora raises it — once,
in the room where it came up, and then not again.

This is the part that looks like a scheduled reminder and isn't.

A heartbeat agent is `schedule → cognition`. A timer fires, the model
wakes, the model produces something. The decision to have a turn was made
by a clock.

Ora is `cognition → intention → schedule → cognition`. It notices something
significant while thinking about the room. It forms an intention — *this
is worth raising, and here is roughly what I'd say*. It chooses when that
should matter. And then, **at the moment it fires, it reads the room again
and may decline.**

That last clause is where the whole difference lives. In our demo the plan
changes on WhatsApp after the reminder is already scheduled on Signal — and
the reminder is withdrawn before it ever fires, because the participation
turn on the other app can see the pending decision the proactive side made
and cancel it. No second raise. No stale nudge arriving after everyone has
moved on.

**A heartbeat gives an agent another turn. Ora decides whether a turn
should exist at all.**

## 5. Now the part where you ask how

Everything above is behaviour. This is the mechanism, and it is one idea.

The loop is four stages and it never stops:

> **OBSERVE** (messages arrive) → **ORIENT** (update the position, update
> what's open) → **DECIDE** (is there an occasion? is something due?) →
> **ACT** (send) → **OBSERVE** again, including its own message.

And the load-bearing sentence:

**Memory is the medium — written at Observe and Act, read at Orient and
Decide.**

No stage hands state to the next stage through a model session. Every
reasoning turn starts fresh. What connects them is durable shared state in
Postgres that any stage can read and any stage can write.

Which gives the architectural claim this project exists to make:

> **The model is disposable; the cognition is not.**

There is no long-lived LLM session anywhere in Ora. Kill the process
mid-run and restart it, swap the model, change the prompt — the group's
context is untouched, because it was never inside the model. It was in the
transcript, the standing position, the open notes, and the decision log.
The model is a function that is called on state and produces state.

## 6. Four kinds of memory, on purpose

It would be easier to say "it has memory". That would be wrong, and the
distinctions are the reason the behaviour works:

| | what it is | authority |
|---|---|---|
| **transcript** | what was actually said, verbatim, with a timestamp | authoritative — this is the record |
| **notes** | what is still unresolved, each with the condition that would close it | active social state — the working list |
| **standing** | one paragraph per room: where this conversation currently stands | an interpretation — it carries an age and it can go stale |
| **people memory** | longer-term sense of the individuals, via Honcho | useful, explicitly not authoritative |

**Record is separate from interpretation.** The transcript cannot be wrong;
the standing paragraph absolutely can, so it is rendered with its age
attached and never treated as fact. A note is not a summary of the past —
it is a claim about the present that carries its own exit condition, which
is how the agent can *close* something rather than remember it forever.

And the closings are honest: a note closes citing a **human's** message,
not the agent's own. Ora's own words can never become a note. It does not
get to mark its own homework.

## 7. What is deliberately missing

No dashboard. No app. No bot badge. Ora speaks as the group's own account,
in the group's own chats, in the group's own language — the demo runs in
Cantonese because that is what the group actually types.

It does not answer everything. It is not trying to be useful every minute.
Most of what it does is decide not to act, and record why.

## 8. What was built today, and what wasn't

Ora is a net-new repository written during the hackathon. Its building
blocks — the relevance-gate instruction and the other judging prompts, the
platform adapters, the Honcho and Exa clients, the schema shapes — come
from an earlier private project by the same author, copied in unedited and
hashed. **What decides was built on the day**: the loop runtime, the gate
and turn wiring, the proactive decide/act pair, the orient stage, the tag
path, the decision log's logic, and every brake.

`MANIFEST.md` in this repo is the evidence: one row per file, marked
`prefab` or `event-day`, with its source. `docs/what-ships-today.md` is the
honest state of the build.

The prompts were not rewritten on the day and that is a deliberate
engineering choice, not a shortcut: they had already been measured against
a 40-case suite, and an unmeasured rewrite would have been worse
engineering wearing a costume of originality. The 12-case subset ships with
its runner (`cases/run_gate.py`) so anyone who edits the gate can measure
it before believing it.

## 9. The line

> It wasn't answering a prompt. It was following the life of the group.
