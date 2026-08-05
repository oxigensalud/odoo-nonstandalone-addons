Results arrive on their own: an hourly scheduled action (*SPMS: obtain
check results*) asks the CCF web service about every posted invoice
sent to SPMS that has no definitive check result yet. A fetched check
document is stored as an input exchange record on the SPMS EDI backend
and processed from there. When the CCF answers that it does not know
an invoice that was sent successfully (return code 301), its result is
created and held in *Error*, with the anomaly explained on the result
form, until a definitive check result supersedes it.

Every invoice sent to SPMS gets its check result attached the moment
the check resolves it: the invoice form shows a smart button with the
error count, opening the result directly (one result per invoice — the
first definitive answer closes the invoice permanently).

The result form carries the check state, the official totals and the
computed official credit value. The *Errors* smart button opens the real
error list — searchable, filterable and grouped by level by default —
since a result can carry hundreds of error rows.

For a result that came back with errors, a draft rectifying invoice is created
through the standard reversal path, cut down to the rejected
prescriptions, and linked back to the result. When the official value
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
