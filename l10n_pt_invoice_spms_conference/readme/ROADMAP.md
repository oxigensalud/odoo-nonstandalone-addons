- The web-service transport (the `edi.output.check` component on the SPMS
  EDI backend), the conference-document parser and the automatic draft
  generation trigger are not wired yet: results and their error rows are
  created by code (see the tests) until they land.
- Error codes are stored as plain codes for now; a shared error-type
  master (auto-creating unknown codes, carrying the per-code noise and
  classification attributes) will replace them.
- A prescription the parser cannot match to an original invoice line
  blocks the generation of its invoice, with no manual exclusion lever:
  revisit if a real case ever needs one.
- The raw conference document is meant to be attached to its result on
  processing, as evidence independent of the exchange-record lifecycle.
