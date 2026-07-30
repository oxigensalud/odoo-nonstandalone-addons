- Assign the *SPMS / Consultation* group to users who can read the SPMS
  returns, and *SPMS / Responsible* to the users allowed to process files
  and generate credit notes.
- Set the *SPMS Adjustment Line Product* on the company (SPMS page, visible
  to *Technical Settings* users): a sale service product required to append
  the adjustment line when an official value differs from the itemised
  total. Credit notes that need it are skipped with a clear message until
  it is configured.
- Set the *SPMS Estimation Tax* on the same company page: the sales VAT
  the SPMS conference applies in its error file (currently the Portuguese
  6% health rate). It drives the with-VAT credit estimate and the
  coherence check of the file's own amounts; processing stops with a
  clear message until it is configured. The credit-note taxes still come
  from the original invoice lines, never from this setting.
- The importer requires the `openpyxl` Python library on the server.
