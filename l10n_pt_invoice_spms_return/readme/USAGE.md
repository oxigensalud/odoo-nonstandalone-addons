Monthly flow, from the *SPMS Issues* menu (Accounting → Customers):

1. Create a new SPMS return, set the conference period (YYYYMM) and attach
   the error report Excel as received from SNS/CCMSNS.
2. Press *Process*. The file is parsed and the full log is built: one line
   per invoice, one row per prescription, one error entry per Excel row.
   Invoices are matched to the original posted customer invoices by the SPMS
   invoice number, and each prescription to its original invoice line.
   Every invoice gets a state (semaphore): awaiting official value, not
   found, error…
3. Review the estimated credit per invoice (formula preview) and enter the
   official credit-note value communicated by the conference result.
   Entering the value confirms it automatically.
4. Press *Create Credit Notes*. For every ready (green) invoice a draft
   rectifying invoice is created through the standard reversal path, cut
   down to the rejected prescriptions, and linked back to the log. When the
   official value differs from the itemised total, an adjustment line
   (service product configured on the company, SPMS page) is appended so the
   total matches the official value exactly; if no line base can reach it
   (global tax rounding), the group tax amount is forced instead (±0.01 max
   deviation from the computed tax). Invoices that cannot be adjusted (e.g.
   adjustment product not configured) are skipped with a clear message.
5. Drafts stay drafts: the accounting team reviews and posts them; the EDI
   circuit takes over from there.

*Process* can be run again at any time: the log is rebuilt from the file
while human input (official values, generated invoices) is preserved.

A processed return is locked: the error file, the period and the date can
only be changed while the return is draft, and neither the return nor its
invoices can be deleted once processed (cancel the return first).

An invoice with rows the module could not fully use — unreadable amounts
(the *Data Error Reason* column tells why), a prescription the matched
invoice does not carry, or a prescription matching several original lines —
is held in *Error* and will not generate a credit note until a corrected
file is processed. The same happens when the original invoice carries a
live credit note this module did not create, or when a prescription is
already claimed by a previous return: the module never adopts or decides —
a human fixes accounting (or the file) and reprocesses.

Once a credit note is generated and alive, the official value of its
invoice is locked; cancel the credit note first to change it.
