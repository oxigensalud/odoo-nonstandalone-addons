Results arrive on their own: the EDI record of every invoice sent to
SPMS expects the check result as its answer (its ACK). An hourly
scheduled action (*SPMS: get conference results from the CCF*) creates
that result record — *Waiting to be received* — at its first pass after
the sending and asks the CCF about it every hour, in its own queue job
(*Queue → Jobs* lists them). *Not checked yet* (302) keeps it waiting; a
check document is received on it and processed from there. The CCF being
unavailable (999), a transport failure, an unknown answer or the CCF not
recognising the invoice (301) put the record in *Error on reception*
with the reason, and it is asked again at the next pass until the CCF
answers; *Retry* on it asks again at the next pass. A job fails only on
a software failure.

Every invoice sent to SPMS gets its check result attached the moment
the check resolves it (one result per invoice — the first definitive
answer closes the invoice permanently). When the check reports errors,
the invoice form shows an *SPMS Check Line Errors* smart button that opens the
result directly; a check without errors adds no button, and its result
is reached from the *Invoice Checks* list.

The result form carries the check state, the official totals and the
computed official credit value. The *Lines* smart button opens the claim
lines — one per prescription, with the billed, allowed and difference
amounts summed at the bottom and the error codes of each — and each line
lists its own errors, code and message; the errors the document anchors
to the invoice itself are listed on the result form. The list opens on
the lines with a difference; remove the filter to see them all.

*Customers → SPMS → Check Line Errors* lists every error the check reported, across
invoices — one row per error with its level, code and message, its
prescription and the billed, allowed and difference amounts of its claim.
The list opens on the errors that carry money: the claims the check cut
or priced above the billed amount, plus the errors anchored to the
document itself; the *Without Difference* filter brings back the
informational ones. Group by error code, level, invoice, customer or
document date to read a month's conference at a glance.

For a result that came back with errors, a draft rectifying invoice is
created automatically the moment the result is processed, through the
standard reversal path, cut down to the affected prescriptions, and
linked back to the result. When the generation of a result fails (a
prescription with no matching invoice line, a foreign note, a
residual beyond the adjustment limit), that result alone is held in
*Error* with the reason on its form; fix the cause and reprocess the
document to retry — the rest of the batch is never dragged along. A
prescription the check priced above the billed amount (a negative
difference) gets its own negative line, so the credit note nets it
against the rejected claims exactly as the official value does; the
result form shows their sum as *Negative Claims*. The claim bases come
from the check document itself, so the draft can only differ from the
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

A negative official value — the check computed more than the invoice
billed — calls for a debit note instead. The module generates it through
the standard debit-note flow, hanging from the invoice as its debit
origin, with the same lines and the sign flipped, the same rounding cent
on the tax line and the same adjustment limit; its lines carry no link to
the sale order, which stays fully invoiced. Everything below applies to
that debit note as it does to the credit note.

Once a note is generated and alive, the official totals of its result
are locked; cancel the note first to change them. Cancelling or deleting
a generated note releases its result immediately — no manual step — and
the cancellation leaves a message in the original invoice's chatter. A
live note of the same kind the module did not create — a credit note for
a positive official value, a debit note for a negative one — holds the
result in *Error* (the result form names it by number): the module never
adopts or decides — a human fixes accounting first.
