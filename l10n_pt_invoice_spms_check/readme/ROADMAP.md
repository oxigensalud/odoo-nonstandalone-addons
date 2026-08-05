- The check-document parser and the automatic draft generation trigger
  are not wired yet: a fetched check document waits as a child input
  exchange record, and results with their error rows are created by
  code (see the tests) until they land.
- Error codes are stored as plain codes for now; a shared error-type
  master (auto-creating unknown codes, carrying the per-code noise and
  classification attributes) will replace them.
- The polling queue has no cap or back-off: an invoice that never gets
  a definitive answer (e.g. a permanent 301 anomaly) is retried every
  pass, forever. Harmless at the current volumes — one read-only call
  per invoice and pass — but revisit if the stuck tail ever grows
  enough to matter.
- A prescription the parser cannot match to an original invoice line
  blocks the generation of its invoice, with no manual exclusion lever:
  revisit if a real case ever needs one.
- The raw check document is meant to be attached to its result on
  processing, as evidence independent of the exchange-record lifecycle.
