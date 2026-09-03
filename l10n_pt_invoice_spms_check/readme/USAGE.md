Results arrive on their own: an hourly scheduled action (*SPMS: get
conference results from the CCF*) asks the CCF web service about every
posted invoice sent to SPMS that has no definitive check result yet. A
fetched check document is stored as an input exchange record on the
SPMS EDI backend and processed from there. When the CCF answers that it
does not know an invoice that was sent successfully (return code 301),
its result is created and held in *Error*, with the incident explained
on the result form, until a definitive check result supersedes it.

Every invoice sent to SPMS gets its check result attached the moment
the check resolves it: the invoice form shows an *SPMS Check* smart
button opening the result directly (one result per invoice — the first
definitive answer closes the invoice permanently).

The result form carries the check state, the official totals and the
computed official credit value. The *Lines* smart button opens the claim
lines — one per prescription, with the billed, allowed and difference
amounts summed at the bottom and the error codes of each — and each line
lists its own errors, code and message; the errors the document anchors
to the invoice itself or to a lot are listed on the result form.

For a result that came back with errors, a draft rectifying invoice is
created automatically the moment the result is processed, through the
standard reversal path, cut down to the rejected prescriptions, and
linked back to the result. When the generation of a result fails
(adjustment product not configured, a prescription with no matching
invoice line, a foreign credit note), that result alone is held in
*Error* with the reason on its form; fix the cause and reprocess the
document to retry — the rest of the batch is never dragged along. When the official value
differs from the itemised total, an adjustment line (service product
configured on the company, SPMS page) is appended so the total matches the
official value exactly; if no line base can reach it (global tax
rounding), the group tax amount is forced instead (±0.01 max deviation
from the computed tax). Drafts stay drafts: the accounting team reviews
and posts them; the EDI circuit takes over from there.

Once a credit note is generated and alive, the official totals of its
result are locked; cancel the credit note first to change them. Cancelling
or deleting a generated credit note releases its result immediately — no
manual step — and the cancellation leaves a note in the original invoice's
chatter. A live credit note the module did not create holds the result in
*Error* (the result form names it by number): the module never adopts or
decides — a human fixes accounting first.
