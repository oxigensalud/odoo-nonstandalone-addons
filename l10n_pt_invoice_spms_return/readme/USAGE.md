Monthly flow, from the *SPMS Issues* menu (Accounting → Customers):

1. Create a new SPMS return, set the conference period (YYYYMM) and attach
   the error report Excel as received from SNS/CCMSNS.
2. Press *Process*. The file is parsed and the full log is built: one line
   per invoice, one row per prescription, one error entry per Excel row.
   Invoices are matched to the original posted customer invoices by the SPMS
   invoice number, and each prescription to its original invoice line.
   Every invoice gets a state (semaphore): awaiting official value, not
   found, mismatch, already done…
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
while human input (official values, resolutions, generated invoices) is
preserved.
