Results arrive on their own: the EDI record of every invoice sent to
SPMS expects the verification result as its answer (its ACK). An hourly
scheduled action (*SPMS: get verification results from the CCF*) creates
that result record — *Waiting to be received* — at its first pass after
the sending and asks the CCF about it every hour, in its own queue job
(*Queue → Jobs* lists them). *Not verified yet* (302) keeps it waiting; a
verification document is received on it and processed from there (a
document naming another invoice than the record's is rejected: *Error on
process* with the reason, *Retry* after looking into it). The CCF being
unavailable (999), a transport failure, an unknown answer or the CCF not
recognising the invoice (301) put the record in *Error on reception*
with the reason, and it is asked again at the next pass until the CCF
answers; *Retry* on it asks again at the next pass. A job fails only on
a software failure.

Every invoice sent to SPMS gets its verification result attached the moment
the verification resolves it (one result per invoice — the first definitive
answer closes the invoice permanently). When the verification reports errors,
the invoice form shows an *SPMS Verification Line Errors* smart button that opens the
result directly; a verification without errors adds no button, and its result
is reached from the *Invoice Verifications* list.

The result form carries the verification state, the official totals and the
computed official credit value. The *Lines* smart button opens the claim
lines — one per prescription, with the billed, allowed and difference
amounts summed at the bottom and the error codes of each — and each line
lists its own errors, code and message; the errors the document anchors
to the invoice itself are listed on the result form. The list opens on
the lines with a difference; remove the filter to see them all.

*Customers → SPMS → Verification Line Errors* lists every error the verification reported, across
invoices — one row per error with its level, code and message, its
prescription and the billed, allowed and difference amounts of its claim.
The list opens on the errors that carry money: the claims the verification cut
or priced above the billed amount, plus the errors anchored to the
document itself; the *Without Difference* filter brings back the
informational ones. Group by error code, level, invoice, customer or
document date to read a month's verification at a glance.

For a result that came back with errors, a draft rectifying invoice is
created automatically the moment the result is processed, through the
standard reversal path, cut down to the affected prescriptions, and
linked back to the result. Each claim edits the copy of its own invoice
line: a prescription billed on several lines is paired with them by
billed quantity and amount, one claim per line, and a claim that finds
no distinct line of its own holds the result instead of guessing. When
the generation of a result fails (a prescription with no matching
invoice line, a foreign note, a residual beyond the adjustment limit),
that result alone is held in *Error* with the reason on its form, and
its exchange record — the result form links to it — in *Error on
process* with the same reason; fix the cause and press *Retry* on that
record: the stored document is processed again at the next pass of the
hourly input action of the EDI framework, and the note is generated
then. The rest of the batch is never dragged along. A
prescription the verification priced above the billed amount (a negative
difference) gets its own negative line, so the credit note nets it
against the rejected claims exactly as the official value does; the
result form shows their sum as *Negative Claims*. The claim bases come
from the verification document itself, so the draft can only differ from the
official value by the rounding of the tax, which the CCF computes once
on the invoice total: that cent is written on the tax line of the draft,
the same correction an accountant would pencil on the tax journal item,
with no extra line, so the total matches the official value exactly. A
residual beyond the *SPMS Adjustment Limit* of the company (0.01 by
default) holds the result in *Error* with the reason, because it reveals
a discrepancy to review rather than rounding noise: a claim priced
differently (the *Claims Credit* of the result then differs from its
official totals before tax), or a company rounding its taxes per line;
raise the limit only when the difference is legitimate and strictly
necessary. Drafts stay drafts: the
accounting team reviews and posts them; the EDI circuit takes over from
there.

A negative official value — the verification computed more than the invoice
billed — calls for a debit note instead. The module generates it through
the standard debit-note flow, hanging from the invoice as its debit
origin, with the same lines and the sign flipped, the same rounding cent
on the tax line and the same adjustment limit; its lines carry no link to
the sale order, which stays fully invoiced. Everything below applies to
that debit note as it does to the credit note.

Once a note is generated and alive, the official totals of its result
are locked; cancel the note first to change them. Cancelling or deleting
a generated note releases its result immediately — no manual step: the
result is *Ready* again, its exchange record is held in *Error on
process* so that *Retry* generates a new note, and the original
invoice's chatter records both. A live note of the same kind the module
did not create — a credit note for a positive official value, a debit
note for a negative one — holds the result in *Error* (the result form
names it by number) and its record with *Retry*: the module never
adopts or decides — a human fixes accounting first. The semaphore
follows every change of the notes on its own; once the foreign note is
gone, *Retry* generates the module's note.
