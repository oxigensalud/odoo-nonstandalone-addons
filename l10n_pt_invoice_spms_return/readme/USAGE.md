Monthly flow, from the *SPMS Issues* menu (Accounting → Customers):

1. Create a new SPMS return, set the conference period (YYYYMM) and attach
   the error report Excel as received from SNS/CCMSNS.
2. Press *Import*. The file is parsed — no accounting lookup happens yet —
   and the full log is built: one line per invoice, one row per
   prescription, one error entry per Excel row.
3. Press *Link*. Invoices are matched to the original posted customer
   invoices by the SPMS invoice number, each prescription to its original
   invoice line, and every invoice gets a state (semaphore): awaiting
   official value, not found, error…
4. Review the estimated credit per invoice (formula preview) and enter the
   official credit-note value communicated by the conference result.
   Entering the value confirms it automatically. A confirmed value of zero
   closes the invoice as *Zero Official Value*: legitimately settled,
   nothing to credit, nothing will be generated for it.
5. Press *Create Credit Notes*. For every ready (green) invoice a draft
   rectifying invoice is created through the standard reversal path, cut
   down to the rejected prescriptions, and linked back to the log. When the
   official value differs from the itemised total, an adjustment line
   (service product configured on the company, SPMS page) is appended so the
   total matches the official value exactly; if no line base can reach it
   (global tax rounding), the group tax amount is forced instead (±0.01 max
   deviation from the computed tax). Invoices that cannot be adjusted (e.g.
   adjustment product not configured) are skipped with a clear message.
6. Drafts stay drafts: the accounting team reviews and posts them; the EDI
   circuit takes over from there.

*Import* can be run again at any time: the log is rebuilt from the file
while human input (official values, generated invoices) is preserved.
*Link* writes only links and states — it deletes nothing, so re-linking is
the natural way to re-evaluate against accounting (e.g. after posting a
missing invoice or cancelling a foreign credit note).

An imported return is locked: the error file, the period and the date can
only be changed while the return is draft, and neither the return nor its
invoices can be deleted once imported (cancel the return first).

An invoice with rows the module could not fully use — unreadable amounts
(the *Data Error Reason* column tells why), a prescription the matched
invoice does not carry, or a prescription matching several original lines —
is held in *Error* and will not generate a credit note until a corrected
file is imported. The same happens when the original invoice carries a
live credit note this module did not create (the invoice form names it by
number), or when a prescription is already claimed by a previous,
non-cancelled return: the module never adopts or decides — a human fixes
accounting (or the file) and re-links.

Once a credit note is generated and alive, the official value of its
invoice is locked; cancel the credit note first to change it.
