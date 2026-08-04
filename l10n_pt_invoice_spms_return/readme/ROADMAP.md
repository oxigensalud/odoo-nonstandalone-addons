- Invoice matching scans all posted customer invoices of the company on
  every *Process* (the SPMS number is derived from the invoice name, not
  stored): fine at the current volume, but worth materialising as a stored
  indexed field if it ever becomes slow.
- Portal results import (module B): the official values will be fillable
  automatically from the SPMS portal «Resultados» file by a separate module
  built on top of this one.
- Reprocessing an *old* return rebuilds its lines, which releases the
  previous-claim links (`previous_line_id`) of newer returns without a
  human decision. Documented limitation, covered by tests.
- A prescription the matched invoice does not carry (line *Not Found*) has
  no manual exclusion lever: the invoice stays in *Error* until a corrected
  file is processed. If a real case ever needs it, a manual exclusion
  mechanism will be added.
